"""Run-scoped gate and review persistence (08 §4.3, §6)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .enums import GateOutcome, ReviewStatus, RiskTier
from .models import (
    GateDecision,
    ReviewItem,
    decision_from_json,
    decision_to_json,
)

__all__ = ["GateDecisionStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_gate_decision (
  candidate_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  bot_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  risk_tier TEXT NOT NULL,
  body TEXT NOT NULL,
  decided_at TEXT NOT NULL,
  PRIMARY KEY (run_id, candidate_id));
CREATE INDEX IF NOT EXISTS evolution_gate_bot_scoped ON evolution_gate_decision (owner_id, bot_id, outcome);
CREATE TABLE IF NOT EXISTS evolution_candidate_risk (
  candidate_id TEXT PRIMARY KEY,
  risk_tier TEXT NOT NULL,
  recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evolution_review_item (
  candidate_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  bot_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  parent_revision_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  gate_decision_id TEXT NOT NULL,
  risk_tier TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT,
  decision_reason TEXT,
  promotion_id TEXT,
  PRIMARY KEY (run_id, candidate_id));
CREATE INDEX IF NOT EXISTS evolution_review_queue_scoped
  ON evolution_review_item (owner_id, bot_id, status, created_at);
CREATE TABLE IF NOT EXISTS evolution_review_request (
  owner_id TEXT NOT NULL,
  bot_id TEXT NOT NULL,
  key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, bot_id, key));
"""


class GateDecisionStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            columns = {
                row["name"]: row["pk"] for row in conn.execute("PRAGMA table_info(evolution_gate_decision)")
            }
            legacy = bool(columns) and not columns["run_id"]
            if legacy:
                for table in ("evolution_gate_decision", "evolution_review_item"):
                    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            if legacy:
                self._migrate_reviews()

    def _migrate_reviews(self) -> None:
        # An old auto decision could have used another binding's ceiling.
        # Preserve its history in the archive, but re-drive under the safe legacy ceiling.
        self.conn.execute(
            "INSERT INTO evolution_gate_decision SELECT g.* FROM evolution_gate_decision_legacy g "
            "JOIN evolution_run r ON r.run_id=g.run_id AND r.owner_id=g.owner_id AND r.bot_id=g.bot_id "
            "JOIN evolution_verdict v ON v.run_id=r.run_id AND v.candidate_id=g.candidate_id "
            "AND v.profile=r.verification_profile AND v.status!='pending' "
            "WHERE CASE WHEN json_valid(g.body) THEN g.outcome IN ('needs_review','not_promotable') "
            "AND json_extract(g.body,'$.outcome')=g.outcome AND json_extract(g.body,'$.risk_tier')=g.risk_tier "
            "AND json_extract(g.body,'$.run_id')=g.run_id "
            "AND json_extract(g.body,'$.owner_id')=g.owner_id AND json_extract(g.body,'$.bot_id')=g.bot_id "
            "AND json_extract(g.body,'$.candidate_id')=g.candidate_id "
            "AND json_extract(g.body,'$.verdict_status')=v.status END"
        )
        self.conn.execute(
            "INSERT INTO evolution_review_item SELECT i.* FROM evolution_review_item_legacy i "
            "JOIN evolution_gate_decision g ON i.run_id=g.run_id AND i.candidate_id=g.candidate_id "
            "AND i.owner_id=g.owner_id AND i.bot_id=g.bot_id AND g.outcome='needs_review' "
            "JOIN evolution_run r ON r.run_id=i.run_id AND r.parent_revision=i.parent_revision_id "
            "JOIN evolution_run_candidate c ON c.run_id=i.run_id AND c.candidate_id=i.candidate_id "
            "AND c.revision=i.revision_id WHERE i.risk_tier=g.risk_tier "
            "AND i.status IN ('open','approved','rejected','superseded') AND i.promotion_id IS NULL"
        )

    def save(self, decision: GateDecision, *, review: ReviewItem | None) -> GateDecision:
        """Persist the decision and any required review together; never reopen a decided review."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO evolution_gate_decision "
                "(candidate_id, owner_id, bot_id, run_id, outcome, risk_tier, body, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, candidate_id) DO NOTHING",
                (
                    decision.candidate_id,
                    decision.owner_id,
                    decision.bot_id,
                    decision.run_id,
                    decision.outcome.value,
                    decision.risk_tier.value,
                    decision_to_json(decision),
                    decision.decided_at.isoformat(),
                ),
            )
            stored = self.get(decision.run_id, decision.candidate_id)
            assert stored is not None
            if stored.outcome is GateOutcome.NEEDS_REVIEW:
                if review is None:
                    raise ValueError("gate decision requires a review item")
                self._open_review(review)
            return stored

    def get(self, run_id: str, candidate_id: str) -> GateDecision | None:
        row = self.conn.execute(
            "SELECT body FROM evolution_gate_decision WHERE run_id=? AND candidate_id=?",
            (run_id, candidate_id),
        ).fetchone()
        return None if row is None else decision_from_json(row["body"])

    def queue(self, *, owner_id: str, bot_id: str) -> list[GateDecision]:
        rows = self.conn.execute(
            "SELECT body FROM evolution_gate_decision WHERE owner_id=? AND bot_id=? AND outcome=? "
            "ORDER BY decided_at DESC",
            (owner_id, bot_id, GateOutcome.NEEDS_REVIEW.value),
        ).fetchall()
        return [decision_from_json(row["body"]) for row in rows]

    # -- 补丁等级（提交时记录，门禁只读） --------------------------------
    def candidate_risk(self, candidate_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT risk_tier FROM evolution_candidate_risk WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        return None if row is None else row["risk_tier"]

    # -- 评审队列（08 §6） ------------------------------------------------
    def _open_review(self, item: ReviewItem) -> None:
        """幂等开单：同一候选重复决策不会产生第二条评审条目。"""

        self.conn.execute(
            "INSERT INTO evolution_review_item (candidate_id, owner_id, bot_id, revision_id, "
            "parent_revision_id, run_id, gate_decision_id, risk_tier, status, created_at, decided_by, "
            "decided_at, decision_reason, promotion_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL) ON CONFLICT(run_id, candidate_id) DO NOTHING",
            (
                item.candidate_id,
                item.owner_id,
                item.bot_id,
                item.revision_id,
                item.parent_revision_id,
                item.run_id,
                item.gate_decision_id,
                item.risk_tier.value,
                item.status.value,
                item.created_at.isoformat(),
            ),
        )

    def get_review(self, run_id: str, candidate_id: str) -> ReviewItem | None:
        row = self.conn.execute(
            "SELECT * FROM evolution_review_item WHERE run_id=? AND candidate_id=?", (run_id, candidate_id)
        ).fetchone()
        return None if row is None else _review_from_row(row)

    def list_reviews(
        self,
        *,
        owner_id: str,
        bot_id: str,
        status: ReviewStatus | None = ReviewStatus.OPEN,
        min_tier: RiskTier | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[ReviewItem]]:
        clauses = ["owner_id=?", "bot_id=?"]
        params: list[object] = [owner_id, bot_id]
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        rows = self.conn.execute(
            "SELECT * FROM evolution_review_item WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, candidate_id",
            params,
        ).fetchall()
        items = [_review_from_row(row) for row in rows]
        if min_tier is not None:
            items = [item for item in items if item.risk_tier.rank >= min_tier.rank]
        start = max(0, (page - 1) * page_size)
        return len(items), items[start : start + page_size]

    def decide_review(
        self,
        run_id: str,
        candidate_id: str,
        *,
        status: ReviewStatus,
        actor: str,
        reason: str,
    ) -> bool:
        """Compare-and-set in the caller's transaction."""
        if not self.conn.in_transaction:
            raise RuntimeError("review decision requires a transaction")
        now = datetime.now(UTC).isoformat()
        cursor = self.conn.execute(
            "UPDATE evolution_review_item SET status=?, decided_by=?, decided_at=?, "
            "decision_reason=? WHERE run_id=? AND candidate_id=? AND status=?",
            (status.value, actor, now, reason, run_id, candidate_id, ReviewStatus.OPEN.value),
        )
        return cursor.rowcount > 0

    def replay(self, owner_id: str, bot_id: str, key: str) -> tuple[str, dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT fingerprint, body FROM evolution_review_request WHERE owner_id=? AND bot_id=? AND key=?",
            (owner_id, bot_id, key),
        ).fetchone()
        return None if row is None else (row["fingerprint"], json.loads(row["body"]))

    def has_legacy_request(self, key: str) -> bool:
        # Legacy responses lack Bot identity and a request fingerprint. Never replay them.
        return (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evolution_review_idempotency'"
            ).fetchone()
            is not None
            and self.conn.execute("SELECT 1 FROM evolution_review_idempotency WHERE key=?", (key,)).fetchone()
            is not None
        )

    def remember(self, owner_id: str, bot_id: str, key: str, fingerprint: str, body: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO evolution_review_request (owner_id, bot_id, key, fingerprint, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, bot_id, key, fingerprint, json.dumps(body), datetime.now(UTC).isoformat()),
        )


def _review_from_row(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        candidate_id=row["candidate_id"],
        owner_id=row["owner_id"],
        bot_id=row["bot_id"],
        revision_id=row["revision_id"],
        parent_revision_id=row["parent_revision_id"],
        run_id=row["run_id"],
        gate_decision_id=row["gate_decision_id"],
        risk_tier=RiskTier(row["risk_tier"]),
        status=ReviewStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        decided_by=row["decided_by"],
        decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        decision_reason=row["decision_reason"],
        promotion_id=row["promotion_id"],
    )
