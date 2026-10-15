"""Migration contract for per-Skill editor approval policy."""

from pathlib import Path

from sqlalchemy import create_engine


def test_policy_migration_covers_existing_and_new_bindings() -> None:
    migration = (
        Path(__file__).parents[5]
        / "src/agentclaw/community/core/skill_center/sql/2026_10_08_skill_editor_auto_approval.sql"
    ).read_text()
    assert "ADD COLUMN auto_approve_editor_requests" in migration
    assert "NOT NULL DEFAULT 0" in migration

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ac_skill_space_binding (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO ac_skill_space_binding (id) VALUES (1)")
        connection.exec_driver_sql(migration)
        connection.exec_driver_sql("INSERT INTO ac_skill_space_binding (id) VALUES (2)")
        rows = connection.exec_driver_sql(
            "SELECT id, auto_approve_editor_requests "
            "FROM ac_skill_space_binding ORDER BY id"
        ).all()
    assert rows == [(1, 0), (2, 0)]
