"""Executable safety tests for the P1-01 Local Installation backfill SQL."""

from pathlib import Path
import sqlite3


_SQL_DIR = (
    Path(__file__).parents[4]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "skill_center"
    / "sql"
)


def _seed_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE ac_skill (
            id INTEGER, avernet_tenant TEXT, env TEXT, user_id TEXT,
            bolt_id TEXT, git_path TEXT
        );
        CREATE TABLE ac_bots (
            id INTEGER, avernet_tenant TEXT, env TEXT, bot_id TEXT, owner_id TEXT,
            is_delete INTEGER
        );
        CREATE TABLE ac_default_skillset_skill_exclusion (
            avernet_tenant TEXT, user_id TEXT, bot_id TEXT, skill_id INTEGER
        );
        CREATE TABLE ac_bot_skill_installation (
            avernet_tenant TEXT, env TEXT, owner_id TEXT, bot_id TEXT,
            skill_id INTEGER
        );
        """
    )


def _dry_run_section(connection: sqlite3.Connection, section: int) -> list[tuple]:
    sql = (
        _SQL_DIR / "2026_08_20_bot_skill_installation_backfill_dry_run.sql"
    ).read_text()
    markers = (
        "-- 1. Archive",
        "-- 2. Every",
        "-- 3. These",
    )
    statement = sql.split(markers[section], 1)[1]
    if section + 1 < len(markers):
        statement = statement.split(markers[section + 1], 1)[0]
    return connection.execute(statement[statement.index("WITH ") :]).fetchall()


def test_dry_run_uses_legacy_exclusion_semantics_without_default_membership() -> None:
    connection = sqlite3.connect(":memory:")
    _seed_schema(connection)
    connection.execute(
        "INSERT INTO ac_bots VALUES (1, 'tenant', 'prod', 'bot', 'owner', 0)"
    )
    connection.executemany(
        "INSERT INTO ac_skill VALUES (?, 'tenant', 'prod', 'owner', 'bot', ?)",
        [
            (1, "local://excluded"),
            (2, "local://active-without-default-membership"),
            (3, "git://market"),
        ],
    )
    connection.execute(
        "INSERT INTO ac_default_skillset_skill_exclusion VALUES ('tenant', 'owner', 'bot', 1)"
    )

    assert _dry_run_section(connection, 2) == [
        ("tenant", "prod", "owner", "bot", 2)
    ]


def test_dry_run_scopes_shared_default_bot_by_live_owner_and_skips_deleted_bot() -> None:
    connection = sqlite3.connect(":memory:")
    _seed_schema(connection)
    connection.executemany(
        "INSERT INTO ac_bots VALUES (?, 'tenant', 'pre', 'default', ?, ?)",
        [(1, "owner-a", 0), (2, "owner-b", 0), (3, "owner-deleted", 1)],
    )
    connection.executemany(
        "INSERT INTO ac_skill VALUES (?, 'tenant', 'pre', ?, 'default', 'local://x')",
        [(11, "owner-a"), (12, "owner-b"), (13, "owner-deleted")],
    )

    assert _dry_run_section(connection, 2) == [
        ("tenant", "pre", "owner-a", "default", 11),
        ("tenant", "pre", "owner-b", "default", 12),
    ]


def test_dry_run_marks_duplicate_live_bot_identity_and_fails_closed() -> None:
    connection = sqlite3.connect(":memory:")
    _seed_schema(connection)
    connection.executemany(
        "INSERT INTO ac_bots VALUES (?, 'tenant', 'pre', 'default', 'owner', 0)",
        [(1,), (2,)],
    )
    connection.execute(
        "INSERT INTO ac_skill VALUES (1, 'tenant', 'pre', 'owner', 'default', 'local://x')"
    )

    assert _dry_run_section(connection, 0) == [("tenant", "pre", 1, 0, 1, 0)]
    assert _dry_run_section(connection, 1) == [
        ("tenant", "pre", "owner", "default", 1, 2)
    ]
    assert _dry_run_section(connection, 2) == []


def test_backfill_is_split_into_read_only_dry_run_and_explicitly_approved_apply() -> None:
    dry_run = (
        _SQL_DIR / "2026_08_20_bot_skill_installation_backfill_dry_run.sql"
    ).read_text()
    apply = (
        _SQL_DIR / "2026_08_20_bot_skill_installation_backfill_apply.sql"
    ).read_text()

    assert "INSERT" not in dry_run.upper()
    assert "START TRANSACTION" not in dry_run.upper()
    assert "ac_skill_set_skill" not in dry_run
    assert "skill_set.is_default" not in apply
    assert "@p1_01_installation_backfill_approved = 0" in apply
    assert "@p1_01_installation_backfill_approved = 1" in apply
    for sql in (dry_run, apply):
        assert "bot.is_delete = 0" in sql
        assert "bot.owner_id = skill.user_id" in sql
        assert "installation.owner_id = candidate.owner_id" in sql or (
            "installation.owner_id = bot.owner_id" in sql
        )
    assert "ac_bot_skill_installation_backfill_run_audit" in apply
    assert "@p1_01_installation_backfill_ambiguous_count = 0" in apply
