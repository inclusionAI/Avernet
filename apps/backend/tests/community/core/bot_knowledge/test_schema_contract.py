"""Pin the reviewed OB schema without executing deployment DDL."""

from datetime import datetime
from pathlib import Path

from sqlalchemy import TIMESTAMP, BigInteger, text
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateTable

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)

SQL_ROOT = Path(__file__).resolve().parents[4] / "sql"


def test_binding_model_matches_reviewed_columns_types_and_indexes():
    table = Binding.__table__
    assert set(table.columns.keys()) == {
        "id",
        "gmt_create",
        "gmt_modified",
        "avernet_tenant",
        "env",
        "bot_pk",
        "space_id",
        "kb_id",
        "enabled",
        "revision",
        "updated_by",
    }
    for name in ("gmt_create", "gmt_modified"):
        assert isinstance(table.c[name].type, TIMESTAMP)
        assert table.c[name].server_default is not None
        assert not table.c[name].nullable
    assert table.c.gmt_create.onupdate is None
    assert table.c.gmt_modified.onupdate is not None
    assert isinstance(table.c.revision.type, BigInteger)
    assert all(column.comment for column in table.columns)
    assert table.comment == "Bot知识库当前启用配置"
    indexes = {
        index.name: tuple(c.name for c in index.columns) for index in table.indexes
    }
    assert indexes == {
        "idx_bk_enabled": (
            "avernet_tenant",
            "env",
            "bot_pk",
            "space_id",
            "enabled",
            "kb_id",
        ),
        "idx_bk_page": ("avernet_tenant", "env", "bot_pk", "space_id", "id"),
    }
    for dialect in (mysql.dialect(), sqlite.dialect()):
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "gmt_create TIMESTAMP" in ddl
        assert "gmt_modified TIMESTAMP" in ddl
        assert "revision BIGINT" in ddl
        assert "DATETIME" not in ddl


def test_ob_table_script_keeps_publish_indexes_separately_deferred():
    ddl = (SQL_ROOT / "2026_10_22_bot_knowledge.sql").read_text()
    assert "`gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP" in ddl
    assert (
        "`gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
        in ddl
    )
    assert "`revision` bigint(20) NOT NULL" in ddl
    assert ddl.count(") GLOBAL") == 3
    assert "COMMENT = 'Bot知识库当前启用配置'" in ddl
    assert "ALTER TABLE" not in ddl
    deferred = (SQL_ROOT / "2026_10_22_bot_knowledge_publish_indexes.sql").read_text()
    assert "DEFERRED" in deferred
    assert "NOT executed" in deferred
    assert "ALTER TABLE ac_bot_publish" in deferred
    assert "idx_publish_current_source(env,source_bot_pk,version,id)" in deferred
    assert (
        "idx_publish_current_default(env,publish_bot_id(512),owner_id,version,id)"
        in deferred
    )


def test_database_defaults_populate_times_and_disabled_state(knowledge):
    db, _, _, _ = knowledge
    with db.orm_session() as session:
        session.execute(
            text("""
            INSERT INTO ac_bot_knowledge_binding
                (env, bot_pk, space_id, kb_id, revision, updated_by)
            VALUES ('dev', 1, 1, 'kb', 1, 'owner')
        """)
        )
    with db.orm_session() as session:
        row = session.query(Binding).one()
        assert row.enabled is False
        assert isinstance(row.gmt_create, datetime)
        assert isinstance(row.gmt_modified, datetime)
        assert row.gmt_create == row.gmt_modified
        assert row.avernet_tenant == "teamclaw"


def test_selection_update_preserves_creation_time_and_refreshes_modified(knowledge):
    db, _, service, scope = knowledge
    first = service.set_enabled(
        scope=scope,
        actor_id="owner",
        kb_id="kb",
        enabled=True,
        expected_revision=0,
    )
    assert set(first) == {
        "bot_id",
        "owner_id",
        "kb_id",
        "enabled",
        "revision",
        "runtime_selection",
    }
    old_time = datetime(2000, 1, 1)
    with db.orm_session() as session:
        row = session.query(Binding).one()
        row.gmt_create = old_time
        row.gmt_modified = old_time
    result = service.set_enabled(
        scope=scope,
        actor_id="owner",
        kb_id="kb",
        enabled=False,
        expected_revision=1,
    )
    assert result["revision"] == 2 and result["enabled"] is False
    with db.orm_session() as session:
        row = session.query(Binding).one()
        assert row.gmt_create == old_time
        assert row.gmt_modified > old_time
