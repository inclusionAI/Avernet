"""Immutable verdicts and pending final delivery, scoped to a submitted Run."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .codec import verdict_from_json, verdict_to_json
from .models import Verdict

__all__ = ["VerdictStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_verdict (
  run_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  profile TEXT NOT NULL,
  status TEXT NOT NULL,
  body TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  final_delivered INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, candidate_id, profile))
"""


class VerdictStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(evolution_verdict)")}
            legacy = bool(columns) and "final_delivered" not in columns
            if legacy:
                conn.execute("ALTER TABLE evolution_verdict RENAME TO evolution_verdict_legacy")
            conn.execute(_SCHEMA)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS evolution_verdict_delivery "
                "ON evolution_verdict (updated_at) WHERE final_delivered=0"
            )
            if legacy:
                # A hash match alone is not proof of submission or Bot ownership.
                conn.execute(
                    "INSERT INTO evolution_verdict (run_id, candidate_id, profile, status, body, updated_at) "
                    "SELECT v.run_id, v.candidate_id, v.profile, v.status, v.body, v.updated_at "
                    "FROM evolution_verdict_legacy v JOIN evolution_run r ON r.run_id=v.run_id "
                    "JOIN evolution_run_candidate c ON c.run_id=r.run_id AND c.candidate_id=v.candidate_id "
                    "WHERE CASE WHEN json_valid(v.body) THEN c.revision != '' AND c.revision=json_extract(v.body,'$.revision') "
                    "AND r.parent_revision=json_extract(v.body,'$.parent_revision') "
                    "AND r.owner_id=json_extract(v.body,'$.bot_owner_id') "
                    "AND r.bot_id=json_extract(v.body,'$.bot_id') AND r.verification_profile=v.profile "
                    "AND v.run_id=json_extract(v.body,'$.run_id') "
                    "AND v.candidate_id=json_extract(v.body,'$.candidate') "
                    "AND v.profile=json_extract(v.body,'$.profile') "
                    "AND v.status=json_extract(v.body,'$.status') "
                    "AND v.status IN ('pending','accept','reject','inconclusive') END"
                )

    def save(self, verdict: Verdict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO evolution_verdict (run_id, candidate_id, profile, status, body, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, candidate_id, profile) DO UPDATE SET "
                "status=excluded.status, body=excluded.body, updated_at=excluded.updated_at "
                "WHERE evolution_verdict.status='pending'",
                (
                    verdict.run_id,
                    verdict.candidate,
                    verdict.profile,
                    verdict.status.value,
                    verdict_to_json(verdict),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def load(self, run_id: str, candidate_id: str, profile: str) -> Verdict | None:
        row = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE run_id=? AND candidate_id=? AND profile=?",
            (run_id, candidate_id, profile),
        ).fetchone()
        return None if row is None else verdict_from_json(row["body"])

    def for_candidate(self, run_id: str, candidate_id: str) -> list[Verdict]:
        rows = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE run_id=? AND candidate_id=? ORDER BY profile",
            (run_id, candidate_id),
        ).fetchall()
        return [verdict_from_json(row["body"]) for row in rows]

    def for_run(self, run_id: str) -> list[Verdict]:
        rows = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE run_id=? ORDER BY candidate_id, profile",
            (run_id,),
        ).fetchall()
        return [verdict_from_json(row["body"]) for row in rows]

    def pending(self) -> list[Verdict]:
        return self._awaiting_delivery(pending=True)

    def undelivered(self) -> list[Verdict]:
        return self._awaiting_delivery(pending=False)

    def _awaiting_delivery(self, *, pending: bool) -> list[Verdict]:
        rows = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE final_delivered=0 "
            "AND (status='pending')=? ORDER BY updated_at",
            (pending,),
        ).fetchall()
        return [verdict_from_json(row["body"]) for row in rows]

    def mark_delivered(self, verdict: Verdict) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE evolution_verdict SET final_delivered=1 "
                "WHERE run_id=? AND candidate_id=? AND profile=? AND status!='pending'",
                (verdict.run_id, verdict.candidate, verdict.profile),
            )
