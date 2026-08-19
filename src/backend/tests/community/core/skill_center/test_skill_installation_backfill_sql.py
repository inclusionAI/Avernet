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
        CREATE TABLE ac_skill_set_skill (
            avernet_tenant TEXT, env TEXT, skill_set_id INTEGER, skill_id INTEGER
        );
        CREATE TABLE ac_skill_set (
            id INTEGER, avernet_tenant TEXT, env TEXT, user_id TEXT,
            bolt_id TEXT, is_default INTEGER
        );
        CREATE TABLE ac_skill (
            id INTEGER, avernet_tenant TEXT, env TEXT, git_path TEXT
        );
        CREATE TABLE ac_default_skillset_skill_exclusion (
            avernet_tenant TEXT, user_id TEXT, bot_id TEXT, skill_set_id INTEGER,
            skill_id INTEGER
        );
        CREATE TABLE ac_bot_skill_installation (
            avernet_tenant TEXT, env TEXT, bot_id TEXT, skill_id INTEGER
        );
        """
    )


def test_dry_run_does_not_backfill_a_skill_excluded_by_a_former_default_set() -> None:
    """The published Local read path treats any matching exclusion as inactive."""
    connection = sqlite3.connect(":memory:")
    _seed_schema(connection)
    connection.execute(
        "INSERT INTO ac_skill_set VALUES (101, 'tenant', 'prod', 'user', 'bot', 1)"
    )
    connection.executemany(
        "INSERT INTO ac_skill VALUES (?, 'tenant', 'prod', ?)",
        [
            (1, "local://stale-exclusion"),
            (2, "local://active"),
            (3, "local://current-exclusion"),
            (4, "git://market"),
            (5, "local://already-installed"),
        ],
    )
    connection.executemany(
        "INSERT INTO ac_skill_set_skill VALUES ('tenant', 'prod', 101, ?)",
        [(1,), (2,), (3,), (4,), (5,)],
    )
    connection.executemany(
        "INSERT INTO ac_default_skillset_skill_exclusion VALUES ('tenant', 'user', 'bot', ?, ?)",
        [
            (99, 1),  # stale former-default exclusion: still inactive on old reads
            (101, 3),
        ],
    )
    connection.execute(
        "INSERT INTO ac_bot_skill_installation VALUES ('tenant', 'prod', 'bot', 5)"
    )

    dry_run = (_SQL_DIR / "2026_08_20_bot_skill_installation_backfill_dry_run.sql").read_text()
    assert connection.execute(dry_run).fetchall() == [("tenant", "prod", "bot", 2)]


def test_backfill_is_split_into_read_only_dry_run_and_explicitly_approved_apply() -> None:
    dry_run = (_SQL_DIR / "2026_08_20_bot_skill_installation_backfill_dry_run.sql").read_text()
    apply = (_SQL_DIR / "2026_08_20_bot_skill_installation_backfill_apply.sql").read_text()

    assert "INSERT" not in dry_run.upper()
    assert "START TRANSACTION" not in dry_run.upper()
    assert "@p1_01_installation_backfill_approved = 0" in apply
    assert "@p1_01_installation_backfill_approved = 1" in apply
    for sql in (dry_run, apply):
        assert "NOT EXISTS" in sql
        assert "exclusion.skill_set_id = skill_set.id" not in sql
