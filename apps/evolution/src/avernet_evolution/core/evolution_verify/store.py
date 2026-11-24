"""判定持久化（07 §4.2）：按 (candidate_id, profile) 幂等，跨重启可读。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .codec import verdict_from_json, verdict_to_json
from .models import Verdict

__all__ = ["SCHEMA", "VerdictStore"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_verdict (
  candidate_id TEXT NOT NULL,
  profile TEXT NOT NULL,
  run_id TEXT,
  status TEXT NOT NULL,
  body TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (candidate_id, profile));
CREATE INDEX IF NOT EXISTS evolution_verdict_run ON evolution_verdict (run_id);
"""


class VerdictStore:
    """与 EvolutionStore 共用同一个 SQLite 连接；判定正文是不可变快照。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.executescript(SCHEMA)

    def save(self, verdict: Verdict) -> None:
        self.conn.execute(
            "INSERT INTO evolution_verdict (candidate_id, profile, run_id, status, body, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(candidate_id, profile) DO UPDATE SET "
            "run_id=excluded.run_id, status=excluded.status, body=excluded.body, "
            "updated_at=excluded.updated_at",
            (
                verdict.candidate,
                verdict.profile,
                verdict.run_id,
                verdict.status.value,
                verdict_to_json(verdict),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()

    def load(self, candidate_id: str, profile: str) -> Verdict | None:
        row = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE candidate_id=? AND profile=?",
            (candidate_id, profile),
        ).fetchone()
        return None if row is None else verdict_from_json(row["body"])

    def for_candidate(self, candidate_id: str) -> list[Verdict]:
        rows = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE candidate_id=? ORDER BY profile", (candidate_id,)
        ).fetchall()
        return [verdict_from_json(row["body"]) for row in rows]

    def for_run(self, run_id: str) -> list[Verdict]:
        rows = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE run_id=? ORDER BY candidate_id, profile",
            (run_id,),
        ).fetchall()
        return [verdict_from_json(row["body"]) for row in rows]

    def pending(self) -> list[Verdict]:
        """重启后仍停在 pending 的判定，供组合根重新驱动（进程内任务不跨重启）。"""

        rows = self.conn.execute(
            "SELECT body FROM evolution_verdict WHERE status='pending' ORDER BY updated_at"
        ).fetchall()
        return [verdict_from_json(row["body"]) for row in rows]
