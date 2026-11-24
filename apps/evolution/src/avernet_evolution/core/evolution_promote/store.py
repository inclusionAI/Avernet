"""门禁决策持久化（08 §4.3）：每个候选一条，重复决策幂等覆盖。"""

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

__all__ = ["SCHEMA", "GateDecisionStore"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_gate_decision (
  candidate_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  bot_id TEXT NOT NULL,
  run_id TEXT,
  outcome TEXT NOT NULL,
  risk_tier TEXT NOT NULL,
  body TEXT NOT NULL,
  decided_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evolution_gate_bot ON evolution_gate_decision (owner_id, bot_id, outcome);
CREATE TABLE IF NOT EXISTS evolution_candidate_risk (
  candidate_id TEXT PRIMARY KEY,
  risk_tier TEXT NOT NULL,
  recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evolution_review_item (
  candidate_id TEXT PRIMARY KEY,
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
  promotion_id TEXT);
CREATE INDEX IF NOT EXISTS evolution_review_queue
  ON evolution_review_item (owner_id, bot_id, status, created_at);
CREATE TABLE IF NOT EXISTS evolution_review_idempotency (
  key TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL);
"""


class GateDecisionStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.executescript(SCHEMA)

    def save(self, decision: GateDecision) -> None:
        self.conn.execute(
            "INSERT INTO evolution_gate_decision "
            "(candidate_id, owner_id, bot_id, run_id, outcome, risk_tier, body, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(candidate_id) DO UPDATE SET outcome=excluded.outcome, "
            "risk_tier=excluded.risk_tier, body=excluded.body, decided_at=excluded.decided_at",
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
        self.conn.commit()

    def get(self, candidate_id: str) -> GateDecision | None:
        row = self.conn.execute(
            "SELECT body FROM evolution_gate_decision WHERE candidate_id=?", (candidate_id,)
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
    def set_candidate_risk(self, candidate_id: str, tier: str) -> None:
        self.conn.execute(
            "INSERT INTO evolution_candidate_risk (candidate_id, risk_tier, recorded_at) "
            "VALUES (?, ?, ?) ON CONFLICT(candidate_id) DO UPDATE SET risk_tier=excluded.risk_tier",
            (candidate_id, tier, datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def candidate_risk(self, candidate_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT risk_tier FROM evolution_candidate_risk WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        return None if row is None else row["risk_tier"]

    # -- 评审队列（08 §6） ------------------------------------------------
    def open_review(self, item: ReviewItem) -> ReviewItem:
        """幂等开单：同一候选重复决策不会产生第二条评审条目。"""

        self.conn.execute(
            "INSERT OR IGNORE INTO evolution_review_item (candidate_id, owner_id, bot_id, revision_id, "
            "parent_revision_id, run_id, gate_decision_id, risk_tier, status, created_at, decided_by, "
            "decided_at, decision_reason, promotion_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
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
        self.conn.commit()
        return self.get_review(item.candidate_id) or item

    def get_review(self, candidate_id: str) -> ReviewItem | None:
        row = self.conn.execute(
            "SELECT * FROM evolution_review_item WHERE candidate_id=?", (candidate_id,)
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
        candidate_id: str,
        *,
        status: ReviewStatus,
        actor: str,
        reason: str,
        promotion_id: str | None = None,
    ) -> bool:
        """只允许从 `open` 迁移一次；返回是否真的改动了状态。"""

        now = datetime.now(UTC).isoformat()
        cursor = self.conn.execute(
            "UPDATE evolution_review_item SET status=?, decided_by=?, decided_at=?, "
            "decision_reason=?, promotion_id=? WHERE candidate_id=? AND status=?",
            (status.value, actor, now, reason, promotion_id, candidate_id, ReviewStatus.OPEN.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def replay(self, key: str | None) -> dict[str, Any] | None:
        """同一幂等键的重试返回第一次的响应，且不做任何新操作（09 §2.3）。"""

        if not key:
            return None
        row = self.conn.execute(
            "SELECT body FROM evolution_review_idempotency WHERE key=?", (key,)
        ).fetchone()
        return None if row is None else json.loads(row["body"])

    def remember(self, key: str | None, body: dict[str, Any]) -> None:
        if not key:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO evolution_review_idempotency (key, body, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(body, sort_keys=True, ensure_ascii=False), datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def supersede(self, candidate_id: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE evolution_review_item SET status=? WHERE candidate_id=? AND status=?",
            (ReviewStatus.SUPERSEDED.value, candidate_id, ReviewStatus.OPEN.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0


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
