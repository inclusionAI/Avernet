"""Durable, immutable human authorization; independent of verdicts and risk reviews."""

import json
import sqlite3
from typing import Any

from avernet_genome.models import BotRef


class ManualAuthorizationStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS evolution_manual_authorization (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL, run_id TEXT NOT NULL, request_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, body TEXT NOT NULL,
                UNIQUE(owner_id, bot_id, request_key))""")

    def replay(self, bot: BotRef, key: str) -> tuple[str, dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT fingerprint, body FROM evolution_manual_authorization "
            "WHERE owner_id=? AND bot_id=? AND request_key=?",
            (bot.owner_id, bot.bot_id, key),
        ).fetchone()
        return (row["fingerprint"], json.loads(row["body"])) if row else None

    def get(
        self, bot: BotRef, candidate_id: str, authorization_id: str | None = None
    ) -> dict[str, Any] | None:
        query = (
            "SELECT body FROM evolution_manual_authorization WHERE owner_id=? AND bot_id=? AND candidate_id=?"
        )
        params = [bot.owner_id, bot.bot_id, candidate_id]
        if authorization_id is not None:
            query += " AND id=?"
            params.append(authorization_id)
        row = self.conn.execute(query + " ORDER BY rowid DESC LIMIT 1", params).fetchone()
        return json.loads(row["body"]) if row else None

    def save(self, bot: BotRef, key: str, fingerprint: str, body: dict[str, Any]) -> None:
        # Caller owns transaction, including the audit event.
        self.conn.execute(
            "INSERT INTO evolution_manual_authorization VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body["id"],
                bot.owner_id,
                bot.bot_id,
                body["candidate_id"],
                body["run_id"],
                key,
                fingerprint,
                json.dumps(body),
            ),
        )
