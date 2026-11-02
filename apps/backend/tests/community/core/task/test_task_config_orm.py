"""ORM + DDL registration tests for ``task_config`` (spec 2026-11-02)."""

from sqlalchemy import create_engine, inspect

from agentclaw.community.core.base import Base
from agentclaw.community.core.schema import import_all_models


def test_task_config_table_registered_and_created():
    import_all_models()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    assert "task_config" in inspector.get_table_names()


def test_task_config_columns_and_constraints():
    import_all_models()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("task_config")}
    assert cols >= {
        "id",
        "config_name",
        "config_type",
        "config_value",
        "env",
        "creator",
        "modifier",
        "gmt_create",
        "gmt_modify",
    }

    uqs = {
        tuple(u["column_names"])
        for u in inspect(engine).get_unique_constraints("task_config")
    }
    assert ("config_name", "env") in uqs

    idxs = {i["name"] for i in inspect(engine).get_indexes("task_config")}
    assert "idx_task_config_env" in idxs
