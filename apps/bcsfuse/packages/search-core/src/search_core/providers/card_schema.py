"""Public SQLite Card schema; production DDL is provisioned by the host."""

TABLE = "bcsfuse_agent_cards"


def initialize_sqlite_cards(conn):
    """Caller holds the initialization transaction."""
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        environment TEXT NOT NULL, agent_id TEXT NOT NULL, card_json TEXT NOT NULL,
        revision INTEGER NOT NULL, profile_json TEXT, config TEXT,
        resource_type TEXT NOT NULL DEFAULT 'agent',
        prepared_revision INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
        preparation_token TEXT NOT NULL DEFAULT '', preparation_key TEXT NOT NULL DEFAULT '',
        lease_until REAL NOT NULL DEFAULT 0,
        gmt_create REAL NOT NULL DEFAULT 0, gmt_modify REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(environment,agent_id))""")
    current = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
    if "resource_type" not in current:
        conn.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN resource_type TEXT NOT NULL DEFAULT 'agent'"
        )
