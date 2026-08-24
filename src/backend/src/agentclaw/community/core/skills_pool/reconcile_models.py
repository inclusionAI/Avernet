"""Stable result types returned by Skills Pool reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SkillsPoolReconcileOutcome(StrEnum):
    POOL_ACTIVE = "pool_active"
    ALREADY_ACTIVE = "already_active"
    NOT_CLAIMED = "not_claimed"
    LEASE_NOT_HELD = "lease_not_held"
    BOT_NOT_FOUND = "bot_not_found"
    BOT_CHANGED = "bot_changed"
    NOT_CAPABLE = "not_capable"
    TRANSIENT_ERROR = "transient_error"
    INVALID = "invalid"
    STATE_RACE_LOST = "state_race_lost"
    DATA_INCONSISTENT = "data_inconsistent"
    ACTIVE_ENTRY_CONFLICT = "active_entry_conflict"
    CUTOVER_FAILED = "cutover_failed"
    MAPPING_FAILED = "mapping_failed"
    MAPPING_VERIFY_FAILED = "mapping_verify_failed"
    DATABASE_COMMIT_FAILED = "database_commit_failed"
    MANUAL_REPAIR_REQUIRED = "manual_repair_required"


@dataclass(frozen=True, slots=True)
class SkillsPoolReconcileResult:
    outcome: SkillsPoolReconcileOutcome
    preparation_id: str | None = None
    evidence: dict[str, object] | None = None
    retryable: bool | None = None


__all__ = ["SkillsPoolReconcileOutcome", "SkillsPoolReconcileResult"]
