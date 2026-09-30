"""Smoke test: the 5 task ORM models register on Base.metadata and build real
SQLite tables with the expected columns and unique indexes."""
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
# Side-effect import: registers the 5 models on Base.metadata.
import agentclaw.community.core.task.repository.models  # noqa: F401


def _engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


def test_five_tables_build_with_key_columns():
    inspector = inspect(_engine())
    for table in (
        "task_info", "task_node", "task_node_run_info",
        "task_node_relation", "task_callback", "task_action_log",
    ):
        assert inspector.has_table(table), f"missing table {table}"

    cols = {c["name"] for c in inspector.get_columns("task_callback")}
    # D5.1 fix: node_id is NOT NULL varchar(128).
    assert "node_id" in cols
    node_id = next(c for c in inspector.get_columns("task_callback") if c["name"] == "node_id")
    assert not node_id["nullable"], "task_callback.node_id must be NOT NULL"

    # Unique indexes present. On SQLite a unique Index (the declaration style
    # used here, mirroring task_queue) surfaces via get_indexes, not
    # get_unique_constraints; union both so the assertion is dialect-agnostic.
    def uniques(table):
        cols = {tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)}
        cols |= {
            tuple(i["column_names"])
            for i in inspector.get_indexes(table)
            if i.get("unique")
        }
        return cols
    assert ("task_id",) in uniques("task_info")
    assert ("task_id", "node_id", "retry") in uniques("task_node_run_info")
    assert ("task_id", "src_node_id", "dst_node_id") in uniques("task_node_relation")
    assert ("run_id", "node_id") in uniques("task_callback")
    assert ("event_id",) in uniques("task_action_log")
    assert ("task_id", "node_id", "seq") in uniques("task_action_log")