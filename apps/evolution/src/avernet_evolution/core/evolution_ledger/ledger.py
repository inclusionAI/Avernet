"""实验记录 H 的 append-only 实现（05-experiment-ledger.zh-CN §11）。

写入即持久；同 (owner, bot, run, candidate) 的重复提交返回同一条目，不再追加。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    Actor,
    ActorKind,
    Cost,
    EntryKind,
    EventType,
    LedgerEntry,
    LedgerEvent,
    VerdictRecord,
    VerdictStatus,
)

__all__ = ["LocalExperimentLedger"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_entries (
  entry_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL, bot_id TEXT NOT NULL, run_id TEXT NOT NULL,
  binding_id TEXT NOT NULL, iteration INTEGER NOT NULL, candidate_id TEXT NOT NULL,
  mechanism TEXT NOT NULL, parent_revision TEXT NOT NULL, candidate_revision TEXT NOT NULL,
  evidence TEXT NOT NULL, patch TEXT NOT NULL, cost TEXT NOT NULL, actor TEXT NOT NULL,
  verdict TEXT, promoted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  UNIQUE (owner_id, bot_id, run_id, candidate_id));
CREATE TABLE IF NOT EXISTS ledger_events (
  entry_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
  data TEXT NOT NULL, idempotency_key TEXT, at TEXT NOT NULL,
  PRIMARY KEY (entry_id, seq));
"""

_ALLOWED = {
    EntryKind.EXPERIMENT: {
        EventType.SUBMITTED: {EventType.VERIFIED, EventType.GATE_DECIDED},
        EventType.VERIFIED: {EventType.GATE_DECIDED},
        EventType.GATE_DECIDED: {
            EventType.PROMOTED,
            EventType.ROLLED_BACK,
            EventType.REJECTED,
        },
        EventType.PROMOTED: {EventType.ROLLED_BACK},
        EventType.ROLLED_BACK: set(),
        EventType.REJECTED: set(),
    }
}


@dataclass
class LocalExperimentLedger:
    path: str | Path = ":memory:"
    conn: sqlite3.Connection = field(init=False)

    def __post_init__(self) -> None:
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    async def record_submission(
        self,
        *,
        bot,
        run_id: str,
        binding_id: str,
        iteration: int,
        candidate_id: str,
        mechanism: str,
        parent_revision: str,
        candidate_revision: str,
        evidence: list[str],
        patch: dict[str, Any],
        cost: Any,
        actor: str,
    ) -> str:
        existing = self.conn.execute(
            "SELECT entry_id FROM ledger_entries WHERE owner_id=? AND bot_id=? AND run_id=? AND candidate_id=?",
            (bot.owner_id, bot.bot_id, run_id, candidate_id),
        ).fetchone()
        if existing is not None:
            return existing["entry_id"]

        import secrets

        entry_id = "exp_" + secrets.token_hex(6)
        now = _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO ledger_entries (entry_id, owner_id, bot_id, run_id, binding_id, iteration, "
                "candidate_id, mechanism, parent_revision, candidate_revision, evidence, patch, cost, actor, "
                "verdict, promoted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)",
                (
                    entry_id,
                    bot.owner_id,
                    bot.bot_id,
                    run_id,
                    binding_id,
                    iteration,
                    candidate_id,
                    mechanism,
                    parent_revision,
                    candidate_revision,
                    json.dumps(evidence),
                    json.dumps(patch),
                    json.dumps(_cost_json(cost)),
                    json.dumps(
                        {
                            "kind": ActorKind.STRATEGY_RUN.value,
                            "actor": actor,
                            "run_id": run_id,
                        }
                    ),
                    now,
                ),
            )
            self._append(
                entry_id,
                LedgerEvent(type=EventType.SUBMITTED, data={"candidate": candidate_id}),
            )
        return entry_id

    async def record_run(self, run: Any) -> str:
        """运行本身只是条目的上下文；此实现不单独建表（见 06 §5.3 的候选记录）。"""

        return run.id

    async def record_verdict(self, entry_id: str, verdict: VerdictRecord) -> int:
        seq = self._append(
            entry_id,
            LedgerEvent(
                type=EventType.VERIFIED,
                data={"status": verdict.status.value, "reason": verdict.reason},
                idempotency_key=f"{entry_id}/verified",
            ),
        )
        self.conn.execute(
            "UPDATE ledger_entries SET verdict=? WHERE entry_id=?",
            (
                json.dumps(
                    {
                        "status": verdict.status.value,
                        "reason": verdict.reason,
                        "aggregates": verdict.aggregates,
                    }
                ),
                entry_id,
            ),
        )
        self.conn.commit()
        return seq

    async def record_promotion(self, entry_id: str, *, revision: str, actor: str) -> int:
        seq = self._append(
            entry_id,
            LedgerEvent(
                type=EventType.PROMOTED,
                data={"revision": revision, "actor": actor},
                idempotency_key=f"{entry_id}/promoted/{revision}",
            ),
        )
        self.conn.execute("UPDATE ledger_entries SET promoted=1 WHERE entry_id=?", (entry_id,))
        self.conn.commit()
        return seq

    async def append(self, entry_id: str, event: LedgerEvent) -> int:
        seq = self._append(entry_id, event)
        self.conn.commit()
        return seq

    def _append(self, entry_id: str, event: LedgerEvent) -> int:
        entry = self.conn.execute("SELECT 1 FROM ledger_entries WHERE entry_id=?", (entry_id,)).fetchone()
        if entry is None:
            raise KeyError(f"unknown ledger entry {entry_id}")
        if event.idempotency_key is not None:
            prior = self.conn.execute(
                "SELECT seq FROM ledger_events WHERE entry_id=? AND type=? AND idempotency_key=?",
                (entry_id, event.type.value, event.idempotency_key),
            ).fetchone()
            if prior is not None:
                return prior["seq"]
        last = self.conn.execute(
            "SELECT seq FROM ledger_events WHERE entry_id=? ORDER BY seq DESC LIMIT 1",
            (entry_id,),
        ).fetchone()
        seq = (last["seq"] + 1) if last is not None else 1
        self.conn.execute(
            "INSERT INTO ledger_events (entry_id, seq, type, data, idempotency_key, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                seq,
                event.type.value,
                json.dumps(event.data),
                event.idempotency_key,
                (event.at or _now()),
            ),
        )
        return seq

    def find_entry(self, *, owner_id: str, bot_id: str, run_id: str, candidate_id: str) -> str | None:
        """判定回写要先定位条目；提交与判定是两条路径，只能按内容哈希对齐。"""

        row = self.conn.execute(
            "SELECT entry_id FROM ledger_entries WHERE owner_id=? AND bot_id=? AND run_id=? "
            "AND candidate_id=?",
            (owner_id, bot_id, run_id, candidate_id),
        ).fetchone()
        return None if row is None else row["entry_id"]

    async def get(self, entry_id: str) -> LedgerEntry:
        row = self.conn.execute("SELECT * FROM ledger_entries WHERE entry_id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown ledger entry {entry_id}")
        events = self.conn.execute(
            "SELECT * FROM ledger_events WHERE entry_id=? ORDER BY seq", (entry_id,)
        ).fetchall()
        return _entry_from_row(row, events)

    async def list(
        self, *, bot=None, run_id: str | None = None, promoted: bool | None = None
    ) -> list[LedgerEntry]:
        where, params = [], []
        if bot is not None:
            where += ["owner_id=?", "bot_id=?"]
            params += [bot.owner_id, bot.bot_id]
        if run_id is not None:
            where.append("run_id=?")
            params.append(run_id)
        if promoted is not None:
            where.append("promoted=?")
            params.append(int(promoted))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.conn.execute(
            f"SELECT * FROM ledger_entries{clause} ORDER BY created_at", params
        ).fetchall()
        return [_entry_from_row(r, []) for r in rows]

    async def export(self, *, bot=None, fmt: str = "filesystem") -> list[dict[str, Any]]:
        entries = await self.list(bot=bot)
        return [
            {
                "entry_id": e.entry_id,
                "run_id": e.run_id,
                "candidate_id": e.candidate_id,
                "verdict": (e.verdict.status.value if e.verdict else None),
                "promoted": e.promoted,
                "patch": e.patch,
            }
            for e in entries
        ]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _cost_json(cost: Any) -> dict[str, Any]:
    if cost is None:
        return {"usd": "0", "tokens": 0, "rollouts": 0}
    if isinstance(cost, dict):
        return {
            "usd": str(cost.get("usd", "0")),
            "tokens": cost.get("tokens", 0),
            "rollouts": cost.get("rollouts", 0),
        }
    return {
        "usd": str(getattr(cost, "usd", "0")),
        "tokens": getattr(cost, "tokens", 0),
        "rollouts": getattr(cost, "rollouts", 0),
    }


def _entry_from_row(row: sqlite3.Row, events: list[sqlite3.Row]) -> LedgerEntry:
    verdict_data = json.loads(row["verdict"]) if row["verdict"] else None
    actor = json.loads(row["actor"])
    return LedgerEntry(
        entry_id=row["entry_id"],
        kind=EntryKind(row["kind"]) if "kind" in row.keys() else EntryKind.EXPERIMENT,  # noqa: SIM118 - sqlite Row membership tests values
        bot_owner_id=row["owner_id"],
        bot_id=row["bot_id"],
        run_id=row["run_id"],
        binding_id=row["binding_id"],
        iteration=row["iteration"],
        candidate_id=row["candidate_id"],
        mechanism=row["mechanism"],
        parent_revision=row["parent_revision"],
        candidate_revision=row["candidate_revision"],
        evidence=tuple(json.loads(row["evidence"])),
        patch=json.loads(row["patch"]),
        cost=Cost(**json.loads(row["cost"])),
        actor=Actor(
            kind=ActorKind(actor["kind"]),
            actor=actor["actor"],
            run_id=actor.get("run_id"),
        ),
        verdict=(
            VerdictRecord(
                status=VerdictStatus(verdict_data["status"]),
                reason=verdict_data.get("reason", ""),
            )
            if verdict_data
            else None
        ),
        promoted=bool(row["promoted"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        events=tuple({"seq": e["seq"], "type": e["type"], "data": json.loads(e["data"])} for e in events),
    )
