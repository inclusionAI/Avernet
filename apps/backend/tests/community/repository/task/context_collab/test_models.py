"""Smoke test: the 2 context collaboration ORM models register on
Base.metadata and build real SQLite tables whose columns mirror the spec §9.5
DDL column-for-column, with the DDL's unique keys, plus tenant-guard
registration."""
from sqlalchemy import inspect

# Side-effect import: registers the 2 models on Base.metadata.
import agentclaw.community.core.task.context_collab.persistence.models  # noqa: F401
from agentclaw.community.utils.avernet_tenant_guard import _GUARDED_MODELS


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _indexes(engine, table: str) -> dict[str, dict]:
    return {i["name"]: i for i in inspect(engine).get_indexes(table)}


def test_both_tables_build_on_sqlite(engine):
    inspector = inspect(engine)
    assert inspector.has_table("ac_context_collab_collet")
    assert inspector.has_table("ac_context_collab_event")


def test_collet_columns_match_ddl(engine):
    assert _columns(engine, "ac_context_collab_collet") == {
        "id",
        "avernet_tenant",
        "env",
        "collab_context_id",
        "create_request_id",
        "context_spec",
        "current_snapshot",
        "current_event_id",
        "context_version",
        "gmt_create",
        "gmt_modified",
    }


def test_collet_unique_keys_match_ddl(engine):
    indexes = _indexes(engine, "ac_context_collab_collet")
    assert tuple(
        indexes["uk_context_collab_collet_context"]["column_names"]
    ) == ("avernet_tenant", "env", "collab_context_id")
    assert indexes["uk_context_collab_collet_context"]["unique"]
    assert tuple(
        indexes["uk_context_collab_collet_request"]["column_names"]
    ) == ("avernet_tenant", "env", "create_request_id")
    assert indexes["uk_context_collab_collet_request"]["unique"]
    assert tuple(
        indexes["idx_context_collab_collet_modified"]["column_names"]
    ) == ("avernet_tenant", "env", "gmt_modified")


def test_event_columns_match_ddl(engine):
    assert _columns(engine, "ac_context_collab_event") == {
        "id",
        "avernet_tenant",
        "env",
        "collab_context_id",
        "event_id",
        "request_id",
        "sequence_no",
        "parent_event",
        "is_trunk",
        "event_kind",
        "primitive_type",
        "actor_ref",
        "visibility",
        "payload",
        "context_version_after",
        "occurred_at",
        "gmt_create",
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
            "uk_context_collab_event_request",
            ("avernet_tenant", "env", "collab_context_id", "request_id"),
            True,
        ),
        (
            "uk_context_collab_event_seq",
            ("avernet_tenant", "env", "collab_context_id", "sequence_no"),
            True,
        ),
        (
            "idx_context_collab_event_parent",
            (
                "avernet_tenant",
                "env",
                "collab_context_id",
                "parent_event",
                "sequence_no",
            ),
            False,
        ),
        (
            "idx_context_collab_event_trunk",
            (
                "avernet_tenant",
                "env",
                "collab_context_id",
                "is_trunk",
                "sequence_no",
            ),
            False,
        ),
    ):
        assert name in indexes, f"missing index {name}"
        assert tuple(indexes[name]["column_names"]) == columns
        assert indexes[name]["unique"] == unique


def test_models_registered_in_tenant_guard():
    from agentclaw.community.core.task.context_collab.persistence.models import (
        ContextCollabColletModel,
        ContextCollabEventModel,
    )

    assert ContextCollabColletModel in _GUARDED_MODELS
    assert ContextCollabEventModel in _GUARDED_MODELS
