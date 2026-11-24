"""Durable, metadata-only execution evidence (not arbitrary strategy stdout).

State transitions currently commit before their journal append. Append failures
propagate: an incomplete journal is NOT a successful/atomic audit transaction.
Host events have no run until a worker actually claims it; reads correlate them
through that claim, never through a speculative queue peek.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .errors import CandidateRejectionCode

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_event (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
 run_id TEXT, job_id TEXT, attempt INTEGER, worker_id TEXT, sandbox_id TEXT,
 authority TEXT NOT NULL, details TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evolution_event_run ON evolution_event(run_id, id);
CREATE INDEX IF NOT EXISTS evolution_event_worker ON evolution_event(worker_id, id);
"""
_KINDS = frozenset(
    {
        "run.queued",
        "run.cancelled",
        "job.claimed",
        "job.heartbeat",
        "job.completed",
        "job.failed",
        "job.requeued",
        "experience.snapshot.persisted",
        "experience.query.completed",
        "candidate.persisted",
        "candidate.rejected",
        "ledger.submission.persisted",
        "sandbox.created",
        "sandbox.exited",
        "sandbox.destroyed",
        "sandbox.destroy_failed",
        "sandbox.execution_failed",
        "worker.log.received",
        "worker.strategy.started",
        "worker.strategy.finished",
        "verdict.finalized",
        "gate.decided",
        "review.decided",
    }
)
_FIELDS = frozenset(
    {
        "image",
        "exit_code",
        "error_type",
        "episode_ids",
        "count",
        "candidate_id",
        "rejection_code",
        "revision",
        "ledger_entry_id",
        "message_sha256",
        "next_job_id",
        "end_reason",
        "verdict_status",
        "gate_outcome",
        "risk_tier",
        "review_status",
        "actor",
    }
)


class ExecutionEventWriteError(RuntimeError):
    """Evidence was not persisted; callers must not report success."""


class ExecutionEvents:
    """Internal journal sharing EvolutionStore's connection; no token/text fields."""

    def __init__(self, conn: sqlite3.Connection, *, initialize: bool = True):
        self.conn = conn
        if initialize:
            conn.executescript(_SCHEMA)

    def append(
        self,
        kind: str,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
        attempt: int | None = None,
        worker_id: str | None = None,
        sandbox_id: str | None = None,
        authority: str = "platform",
        details: dict[str, Any] | None = None,
    ) -> None:
        data = details or {}
        if kind not in _KINDS or set(data) - _FIELDS:
            raise ValueError("unknown execution event or unsafe metadata field")
        if authority not in ("platform", "worker-reported"):
            raise ValueError("unknown event authority")
        if "rejection_code" in data:
            CandidateRejectionCode(data["rejection_code"])
        encoded = json.dumps(data, sort_keys=True)
        if len(encoded) > 32768:
            raise ValueError("execution metadata too large")
        try:
            self.conn.execute(
                "INSERT INTO evolution_event "
                "(at,kind,run_id,job_id,attempt,worker_id,sandbox_id,authority,details) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(UTC).isoformat(),
                    kind,
                    run_id,
                    job_id,
                    attempt,
                    worker_id,
                    sandbox_id,
                    authority,
                    encoded,
                ),
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            self.conn.rollback()
            raise ExecutionEventWriteError("execution evidence write failed") from exc

    def for_run(self, run_id: str, *, after: int = 0, limit: int = 500) -> list[dict]:
        if not 1 <= limit <= 1000 or after < 0:
            raise ValueError("invalid event cursor/limit")
        rows = self.conn.execute(
            "SELECT * FROM evolution_event WHERE id>? AND (run_id=? OR "
            "(run_id IS NULL AND worker_id IN (SELECT worker_id FROM evolution_event "
            "WHERE run_id=? AND kind='job.claimed'))) ORDER BY id LIMIT ?",
            (after, run_id, run_id, limit),
        ).fetchall()
        return [dict(row) | {"details": json.loads(row["details"])} for row in rows]
