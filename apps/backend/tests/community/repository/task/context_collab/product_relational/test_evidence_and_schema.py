from dataclasses import replace

import pytest
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
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
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
    assert "fk_cc_rollout_collet" in str(
        CreateTable(ColletRolloutRow.__table__).compile(dialect=dialect)
    )


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
