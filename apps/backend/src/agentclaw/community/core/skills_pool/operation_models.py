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
    engine: str | None = None
    batch_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        value = {"owner_id": self.owner_id, "bot_id": self.bot_id}
        if self.engine is not None:
            value["engine"] = self.engine
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
    schema_version: int
    engine_admission: dict[str, bool]
    bot_allowlist: tuple[RolloutBotEntry, ...]
    owner_rollouts: tuple[RolloutOwnerEntry, ...]
    environment_rollouts: tuple[str, ...]
    bot_exclusions: tuple[RolloutBotEntry, ...]
    audit_log: tuple[RolloutAuditEvent, ...]
    legacy_teclaw_controls: tuple[RolloutBotEntry, ...] = ()

    @property
    def promoted_engines(self) -> tuple[str, ...]:
        """v1 read compatibility for historical batch reporting."""

        return tuple(
            engine for engine, enabled in self.engine_admission.items() if enabled
        )

    @property
    def whitelist(self) -> tuple[RolloutBotEntry, ...]:
        return self.bot_allowlist

    @property
    def negative_controls(self) -> tuple[RolloutBotEntry, ...]:
        return self.bot_exclusions

    @property
    def teclaw_controls(self) -> tuple[RolloutBotEntry, ...]:
        return self.legacy_teclaw_controls

    @property
    def full_rollout_engines(self) -> tuple[str, ...]:
        return self.environment_rollouts

    @property
    def full_rollout_owners(self) -> tuple[RolloutOwnerEntry, ...]:
        return self.owner_rollouts


@dataclass(frozen=True, slots=True)
class WhitelistMutationResult:
    changed: bool
    claimed_before: bool
    claimed_after: bool
    snapshot: RolloutConfigSnapshot
