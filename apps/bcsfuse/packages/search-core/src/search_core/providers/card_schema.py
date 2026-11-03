"""Public SQLite Card schema; production DDL is provisioned by the host."""

import json

TABLE = "bcsfuse_agent_cards"


def initialize_sqlite_cards(conn):
    """Caller holds the initialization transaction."""
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        environment TEXT NOT NULL, agent_id TEXT NOT NULL, card_json TEXT NOT NULL,
        revision INTEGER NOT NULL, profile_json TEXT, config TEXT,
        resource_type TEXT NOT NULL DEFAULT 'agent',
        is_deleted INTEGER NOT NULL DEFAULT 0,
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
    if "is_deleted" not in current:
        conn.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0"
        )
        # One-time local schema upgrade, never consult JSON for deletion at runtime.
        reader = conn.execute(f"SELECT environment,agent_id,card_json FROM {TABLE}")
        try:
            while rows := reader.fetchmany(100):
                for environment, agent_id, encoded in rows:
                    data = json.loads(encoded)
                    deleted = data.pop("deleted", data.get("status") == "inactive")
                    conn.execute(
                        f"UPDATE {TABLE} SET is_deleted=?,card_json=? WHERE environment=? AND agent_id=?",
                        (
                            int(bool(deleted)),
                            json.dumps(data, ensure_ascii=False),
                            environment,
                            agent_id,
                        ),
                    )
        finally:
            reader.close()
