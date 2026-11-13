"""Collet, Task and Event storage matches baseline plus Task migration DDL.

Build real SQLite tables and verify columns, indexes and tenant registration.
"""

from sqlalchemy import inspect

# Side-effect import: registers the 3 models on Base.metadata.
import agentclaw.community.core.task.context_collab.persistence.models  # noqa: F401
from agentclaw.community.utils.avernet_tenant_guard import _GUARDED_MODELS


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _indexes(engine, table: str) -> dict[str, dict]:
    return {i["name"]: i for i in inspect(engine).get_indexes(table)}


def test_all_three_tables_build_on_sqlite(engine):
    inspector = inspect(engine)
    assert inspector.has_table("ac_context_collab_collet")
    assert inspector.has_table("ac_context_collab_event")
    assert inspector.has_table("ac_context_collab_task")


def test_collet_columns_match_ddl(engine):
    assert _columns(engine, "ac_context_collab_collet") == {
        "id",
        "avernet_tenant",
        "env",
        "collab_context_id",
        "actor_ref",
        "context_spec",
        "current_snapshot",
        "root_task_ref",
        "current_event_id",
        "context_version",
        "gmt_create",
        "gmt_modified",
    }


def test_collet_unique_keys_match_ddl(engine):
    indexes = _indexes(engine, "ac_context_collab_collet")
    assert tuple(indexes["uk_context_collab_collet_context"]["column_names"]) == (
        "avernet_tenant",
        "env",
        "collab_context_id",
    )
    assert indexes["uk_context_collab_collet_context"]["unique"]
    assert tuple(indexes["idx_context_collab_collet_modified"]["column_names"]) == (
        "avernet_tenant",
        "env",
        "gmt_modified",
    )


def test_event_columns_match_ddl(engine):
    assert _columns(engine, "ac_context_collab_event") == {
        "id",
        "avernet_tenant",
        "env",
        "collab_context_id",
        "event_id",
        "task_ref",
        "task_version_after",
        "parent_event",
        "event_kind",
        "primitive_type",
        "actor_ref",
        "visibility",
        "payload",
        "context_version_after",
        "occurred_at",
        "gmt_create",
        "gmt_modified",
    }


def test_event_unique_keys_match_ddl(engine):
    indexes = _indexes(engine, "ac_context_collab_event")
    for name, columns, unique in (
        (
            "uk_context_collab_event",
            ("avernet_tenant", "env", "collab_context_id", "event_id"),
            True,
        ),
        (
            "idx_context_collab_event_parent",
            (
                "avernet_tenant",
                "env",
                "collab_context_id",
                "parent_event",
            ),
            False,
        ),
    ):
        assert name in indexes, f"missing index {name}"
        assert tuple(indexes[name]["column_names"]) == columns
        assert indexes[name]["unique"] == unique

    # 已删除的幂等/序号/主干键不复存在(作者裁决)。
    for removed in (
        "uk_context_collab_event_request",
        "uk_context_collab_event_seq",
        "idx_context_collab_event_trunk",
    ):
        assert removed not in indexes
        assert name in indexes, f"missing index {name}"
        assert tuple(indexes[name]["column_names"]) == columns
        assert indexes[name]["unique"] == unique


def test_task_columns_match_ddl(engine):
    assert _columns(engine, "ac_context_collab_task") == {
        "id",
        "avernet_tenant",
        "env",
        "task_id",
        "collab_context_id",
        "parent_task_ref",
        "task_version",
        "task_data",
        "gmt_create",
        "gmt_modified",
    }


def test_task_indexes_match_ddl(engine):
    indexes = _indexes(engine, "ac_context_collab_task")
    assert tuple(indexes["uk_context_collab_task"]["column_names"]) == (
        "avernet_tenant",
        "env",
        "task_id",
    )
    assert indexes["uk_context_collab_task"]["unique"]
    assert tuple(indexes["idx_context_collab_task_context"]["column_names"]) == (
        "avernet_tenant",
        "env",
        "collab_context_id",
    )
    assert not indexes["idx_context_collab_task_context"]["unique"]


def test_models_registered_in_tenant_guard():
    from agentclaw.community.core.task.context_collab.persistence.models import (
        ContextCollabColletModel,
        ContextCollabEventModel,
        ContextCollabTaskModel,
    )

    assert ContextCollabColletModel in _GUARDED_MODELS
    assert ContextCollabEventModel in _GUARDED_MODELS
    assert ContextCollabTaskModel in _GUARDED_MODELS
