"""实验记录 H 的领域模型（05-experiment-ledger.zh-CN §2、§3）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "Actor",
    "ActorKind",
    "Cost",
    "EntryKind",
    "EvidenceRef",
    "LedgerEntry",
    "LedgerEvent",
    "VerdictRecord",
    "VerdictStatus",
]


class EntryKind(StrEnum):
    EXPERIMENT = "experiment"


class VerdictStatus(StrEnum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


class ActorKind(StrEnum):
    USER = "user"
    STRATEGY_RUN = "strategy_run"
    PLATFORM = "platform"


class EventType(StrEnum):
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    GATE_DECIDED = "gate_decided"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    actor: str
    run_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "actor": self.actor, "run_id": self.run_id}


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    id: str


@dataclass(frozen=True)
class Cost:
    usd: str = "0"
    tokens: int = 0
    rollouts: int = 0


@dataclass(frozen=True)
class VerdictRecord:
    status: VerdictStatus
    reason: str = ""
    aggregates: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LedgerEvent:
    type: EventType
    data: dict[str, Any]
    at: datetime | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    kind: EntryKind
    bot_owner_id: str
    bot_id: str
    run_id: str
    binding_id: str
    iteration: int
    candidate_id: str
    mechanism: str
    parent_revision: str
    candidate_revision: str
    evidence: tuple[EvidenceRef, ...]
    patch: dict[str, Any]
    cost: Cost
    actor: Actor
    verdict: VerdictRecord | None
    promoted: bool
    created_at: datetime
    events: tuple[dict[str, Any], ...] = ()
