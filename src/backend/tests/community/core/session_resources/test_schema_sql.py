"""Contracts for the session-resource MySQL table indexes."""
from pathlib import Path


SQL_DIR = (
    Path(__file__).resolve().parents[4]
    / "src/agentclaw/community/core/session_resources/sql"
)


def _normalized_sql(filename: str) -> str:
    return " ".join((SQL_DIR / filename).read_text().lower().split())


def test_fresh_schema_allows_multiple_resources_per_session() -> None:
    schema = _normalized_sql("ac_session_resource.sql")

    assert (
        "key idx_session_resource_owner_bot_session "
        "(owner_id, bot_id, session_key_hash)"
    ) in schema
    assert "unique key idx_session_resource_owner_bot_session" not in schema


def test_migration_replaces_historical_session_unique_key() -> None:
    migration = _normalized_sql(
        "2026_07_30_fix_session_resource_owner_bot_session_index.sql"
    )

    assert "drop index uk_idx_session_resource_owner_bot_session" in migration
    assert (
        "add key idx_session_resource_owner_bot_session "
        "(owner_id, bot_id, session_key_hash)"
    ) in migration
