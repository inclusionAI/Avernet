"""Public SQLite Card schema; production DDL is provisioned by the host."""

import json
import re

TABLE = "bcsfuse_agent_cards"


def validate_card_table(table):
    if not isinstance(table, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]{0,63}", table
    ):
        raise ValueError("invalid catalog table")
    return table


def initialize_sqlite_cards(conn, *, table=TABLE):
    """Caller holds the initialization transaction."""
    table = validate_card_table(table)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
        environment TEXT NOT NULL, agent_id TEXT NOT NULL, card_json TEXT NOT NULL,
        revision INTEGER NOT NULL, profile_json TEXT, config TEXT,
        resource_type TEXT NOT NULL DEFAULT 'agent',
        is_deleted INTEGER NOT NULL DEFAULT 0,
        prepared_revision INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
        preparation_token TEXT NOT NULL DEFAULT '', preparation_key TEXT NOT NULL DEFAULT '',
        lease_until REAL NOT NULL DEFAULT 0,
        gmt_create REAL NOT NULL DEFAULT 0, gmt_modify REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(environment,agent_id))""")
    current = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "labels" not in current:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN labels TEXT")
    if "resource_type" not in current:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN resource_type TEXT NOT NULL DEFAULT 'agent'"
        )
    if "is_deleted" not in current:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0"
        )
        # One-time local schema upgrade, never consult JSON for deletion at runtime.
        reader = conn.execute(f"SELECT environment,agent_id,card_json FROM {table}")
        try:
            while rows := reader.fetchmany(100):
                for environment, agent_id, encoded in rows:
                    data = json.loads(encoded)
                    deleted = data.pop("deleted", data.get("status") == "inactive")
                    conn.execute(
                        f"UPDATE {table} SET is_deleted=?,card_json=? WHERE environment=? AND agent_id=?",
                        (
                            int(bool(deleted)),
                            json.dumps(data, ensure_ascii=False),
                            environment,
                            agent_id,
                        ),
                    )
        finally:
            reader.close()
