from dataclasses import replace
from pathlib import Path
import re

import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.runtime_relational_codec import (
    encode_evidence,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence import (
    product_relational_models,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
    BUSINESS_TABLES,
    ProductProfileRow,
    ColletRow,
    ColletRolloutRow,
    StageRolloutRow,
    EventRow,
)


def test_exact_schema_types_defaults_keys_and_unsigned_parent():
    stage = StageRolloutRow.__table__
    assert set(stage.columns.keys()) == {
        "id",
        "avernet_tenant",
        "env",
        "collet_rollout_id",
        "name",
        "status",
        "assignee_type",
        "assignee_uuid",
        "input",
        "session_id",
        "acceptances",
        "error_msg",
        "artifacts",
        "gmt_create",
        "gmt_modified",
    }
    dialect = mysql.dialect()
    assert str(stage.c.acceptances.type.compile(dialect=dialect)) == "VARCHAR(8192)"
    assert str(stage.c.error_msg.type.compile(dialect=dialect)) == "TEXT"
    assert str(stage.c.artifacts.type.compile(dialect=dialect)) == "MEDIUMTEXT"
    assert "UNSIGNED" in str(ColletRow.id.type.compile(dialect=dialect))
    assert "UNSIGNED" in str(ColletRolloutRow.collet_id.type.compile(dialect=dialect))
    profile = ProductProfileRow.__table__
    assert str(profile.c.config.type.compile(dialect=dialect)) == "TEXT"
    indexes = {index.name: index for index in profile.indexes}
    assert set(indexes) == {"idx_cc_profile_scope_name"}
    assert not indexes["idx_cc_profile_scope_name"].unique
    assert [c.name for c in indexes["idx_cc_profile_scope_name"].columns] == [
        "avernet_tenant",
        "env",
        "name",
    ]
    assert (
        profile.c.status.default.arg == profile.c.status.server_default.arg == "DRAFT"
    )
    assert "owner_json" not in profile.c and "config_digest" not in profile.c
    assert profile.c.creator.nullable and profile.c.modifer.nullable
    event = EventRow.__table__
    assert "interactive_key" not in event.c
    assert event.c.event_id.type.length == 128 and not event.c.event_id.nullable
    index = next(
        index for index in event.indexes if index.name == "uk_context_collab_event"
    )
    assert index.unique
    assert [c.name for c in index.columns] == [
        "avernet_tenant",
        "env",
        "collab_context_id",
        "event_id",
    ]


def test_business_schema_has_no_foreign_keys_in_metadata_mysql_or_sqlite(business):
    inspector = inspect(business.engine)
    with business.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    for table in BUSINESS_TABLES:
        assert not table.foreign_keys, table.name
        assert not table.foreign_key_constraints, table.name
        ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
        assert not re.search(r"\b(?:FOREIGN\s+KEY|REFERENCES)\b", ddl, re.I)
        assert inspector.get_foreign_keys(table.name) == []


def test_additive_migration_has_no_foreign_key_clauses():
    migration = (
        Path(product_relational_models.__file__).parents[2]
        / "sql"
        / "2026_11_27_product_relational_storage.sql"
    ).read_text()
    assert not re.search(r"\b(?:FOREIGN\s+KEY|REFERENCES)\b", migration, re.I)


@pytest.mark.parametrize(
    "model,expected",
    [
        (
            ColletRolloutRow,
            {
                "uk_cc_rollout_order": (["collet_id", "ordinal"], True),
                "uk_cc_rollout_collet_id": (["collet_id", "id"], True),
                "idx_cc_rollout_predecessor": (["collet_id", "predecessor_id"], False),
                "idx_cc_rollout_scope_collet": (
                    ["avernet_tenant", "env", "collet_id", "id"],
                    False,
                ),
            },
        ),
        (
            StageRolloutRow,
            {
                "idx_cc_stage_scope_rollout": (
                    ["avernet_tenant", "env", "collet_rollout_id", "id"],
                    False,
                ),
            },
        ),
    ],
)
def test_rollout_relationship_indexes_are_preserved(business, model, expected):
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in model.__table__.indexes
    } == expected
    assert {
        index["name"]: (index["column_names"], bool(index["unique"]))
        for index in inspect(business.engine).get_indexes(model.__tablename__)
    } == expected


def test_new_ordinary_indexes_use_idx_prefix_and_stage_has_no_unique_key(business):
    for table in BUSINESS_TABLES:
        for index in table.indexes:
            if not index.unique:
                assert index.name.startswith("idx_"), (table.name, index.name)
            assert not index.name.startswith("ix_cc_")

        for index in inspect(business.engine).get_indexes(table.name):
            if not index["unique"]:
                assert index["name"].startswith("idx_"), (table.name, index["name"])

    stage = StageRolloutRow.__table__
    assert stage.indexes and not any(index.unique for index in stage.indexes)
    migration = (
        Path(product_relational_models.__file__).parents[2]
        / "sql"
        / "2026_11_27_product_relational_storage.sql"
    ).read_text()
    assert "ix_cc_" not in migration
    assert not re.search(r"\bUNIQUE\s+KEY\s+uk_cc_stage_", migration, re.I)


@pytest.mark.parametrize("filler", ["x", "字", '"', "\\"])
def test_acceptances_final_json_8192_character_boundary(filler):
    base = AcceptanceResult("condition", True, "")
    wire = encode_evidence([base], [], "")[0]
    units = 2 if filler in {'"', "\\"} else 1
    repeat = (8192 - len(wire)) // units
    value = replace(base, reason=filler * repeat + "x" * ((8192 - len(wire)) % units))
    assert len(encode_evidence([value], [], "")[0]) == 8192
    with pytest.raises(Invalid, match="8192"):
        encode_evidence([replace(value, reason=value.reason + "x")], [], "")


def test_error_text_65535_utf8_bytes_not_characters():
    assert len(encode_evidence([], [], "字" * 21845)[2].encode()) == 65535
    with pytest.raises(Invalid, match="byte limit"):
        encode_evidence([], [], "字" * 21845 + "x")
    assert encode_evidence([], [], "x" * 65535)[2] == "x" * 65535
    with pytest.raises(Invalid):
        encode_evidence([], [], "x" * 65536)
    assert encode_evidence([], [], "") == ("[]", "[]", "")


@pytest.mark.parametrize(
    "invalid",
    [
        '[{"condition":"ok","passed":true,"reason":"","extra":1}]',
        '[{"condition":"a","condition":"b","passed":true,"reason":""}]',
        "[NaN]",
        "{}",
        "[null]",
    ],
)
def test_evidence_read_rejects_unknown_duplicate_nonfinite_and_nonarray(
    online, invalid
):
    h = online
    history = h.repo.create_collet(h.profile.id, "work", h.owner)
    with h.db.orm_session() as session:
        row = session.query(StageRolloutRow).first()
        row.acceptances = invalid
    with pytest.raises(Invalid):
        h.repo.get_collet(history.collet.id, h.owner)
