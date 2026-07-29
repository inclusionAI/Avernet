"""Value objects exposed by the Skills Pool rollout operator service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RolloutControlGroup(StrEnum):
    NEGATIVE = "negative"
    TECLAW = "teclaw"


class RolloutOperationError(ValueError):
    """An operator request cannot be safely applied."""


@dataclass(frozen=True, slots=True)
class RolloutBotEntry:
    owner_id: str
    bot_id: str
    batch_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        value = {"owner_id": self.owner_id, "bot_id": self.bot_id}
        if self.batch_id is not None:
            value["batch_id"] = self.batch_id
        return value


@dataclass(frozen=True, slots=True)
class RolloutOwnerEntry:
    owner_id: str
    engine: str

    def to_dict(self) -> dict[str, str]:
        return {"owner_id": self.owner_id, "engine": self.engine}


@dataclass(frozen=True, slots=True)
class RolloutAuditEvent:
    env: str
    action: str
    operator: str
    reason: str
    batch_id: str | None
    based_on_config_version: str | None
    effective_config_version: str
    effective_at: str
    evidence: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "env": self.env,
            "action": self.action,
            "operator": self.operator,
            "reason": self.reason,
            "batch_id": self.batch_id,
            "based_on_config_version": self.based_on_config_version,
            "effective_config_version": self.effective_config_version,
            "effective_at": self.effective_at,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class BatchPromotionEvidence:
    engine: str
    batch_id: str
    promotion_ready: bool
    report: dict[str, object]


@dataclass(frozen=True, slots=True)
class RolloutConfigSnapshot:
    env: str
    config_id: int | None
    config_version: str | None
    record_version: str | None
    config_revision: str | None
    enabled: bool
    enable_all: bool
    promoted_engines: tuple[str, ...]
    whitelist: tuple[RolloutBotEntry, ...]
    negative_controls: tuple[RolloutBotEntry, ...]
    teclaw_controls: tuple[RolloutBotEntry, ...]
    audit_log: tuple[RolloutAuditEvent, ...]
    full_rollout_engines: tuple[str, ...] = ()
    full_rollout_owners: tuple[RolloutOwnerEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class WhitelistMutationResult:
    changed: bool
    claimed_before: bool
    claimed_after: bool
    snapshot: RolloutConfigSnapshot
