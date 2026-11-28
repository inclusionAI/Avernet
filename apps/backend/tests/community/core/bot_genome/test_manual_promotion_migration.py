"""An existing SQLite installation can apply the additive authorization migration."""

import sqlite3
from pathlib import Path

from agentclaw.community.core.bot_genome import repository


def test_existing_rows_survive_authorization_migration():
    migration = (
        Path(repository.__file__).resolve().parent.parent
        / "sql/2026_11_26_manual_promotion.sql"
    ).read_text()
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE ac_genome_promotion "
            "(promotion_id TEXT PRIMARY KEY, reason TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO ac_genome_promotion VALUES (?, ?)",
            ("existing-promotion", "ordinary promotion"),
        )
        connection.executescript(migration)
        assert connection.execute(
            "SELECT promotion_id, reason, authorization FROM ac_genome_promotion"
        ).fetchone() == ("existing-promotion", "ordinary promotion", None)
        authorization = '{"basis":"human","id":"example-authorization"}'
        connection.execute(
            "INSERT INTO ac_genome_promotion VALUES (?, ?, ?)",
            ("manual-promotion", "human intervention", authorization),
        )
        assert connection.execute(
            "SELECT authorization FROM ac_genome_promotion WHERE promotion_id = ?",
            ("manual-promotion",),
        ).fetchone() == (authorization,)
