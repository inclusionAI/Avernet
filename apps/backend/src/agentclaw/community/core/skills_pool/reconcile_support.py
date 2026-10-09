"""Skills Pool reconcile 编排共用的纯函数。"""

from __future__ import annotations

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.models import PoolCutoverStatus
from agentclaw.community.core.skills_pool.types import BotSkillLayoutState


def probe_outcome(status: RuntimeLayoutProbeStatus) -> str:
    return {
        RuntimeLayoutProbeStatus.NOT_CAPABLE: "not_capable",
        RuntimeLayoutProbeStatus.TRANSIENT_ERROR: "transient_error",
        RuntimeLayoutProbeStatus.INVALID: "invalid",
    }.get(status, "invalid")


def cutover_failure_profile(
    status: PoolCutoverStatus,
) -> tuple[str, str, bool] | None:
    return {
        PoolCutoverStatus.DATA_INCONSISTENT: (
            "data_inconsistent",
            "pre_cutover_validation",
            False,
        ),
        PoolCutoverStatus.ACTIVE_ENTRY_CONFLICT: (
            "active_entry_conflict",
            "pre_cutover_validation",
            False,
        ),
        PoolCutoverStatus.NOT_ATOMIC: (
            "cutover_failed",
            "atomic_cutover",
            False,
        ),
        PoolCutoverStatus.INVALID: (
            "cutover_failed",
            "pre_cutover_validation",
            False,
        ),
        PoolCutoverStatus.TRANSIENT_ERROR: (
            "cutover_failed",
            "pre_cutover_filesystem",
            True,
        ),
    }.get(status)


def post_commit_cutover_failure_profile(
    status: PoolCutoverStatus,
) -> tuple[str, str, bool]:
    """Classify refresh failures after the irreversible boundary is known."""

    if status in {
        PoolCutoverStatus.UNKNOWN,
        PoolCutoverStatus.TRANSIENT_ERROR,
        PoolCutoverStatus.POST_CUTOVER_SYNC_PENDING,
    }:
        return "cutover_failed", "post_cutover_refresh", True
    profile = cutover_failure_profile(status)
    if profile is not None:
        outcome, _, retryable = profile
        return outcome, "post_cutover_refresh", retryable
    return "cutover_failed", "post_cutover_refresh", False


def persisted_cutover_evidence(
    state: BotSkillLayoutState,
) -> dict[str, object] | None:
    probe_evidence = state.last_probe_evidence
    if not isinstance(probe_evidence, dict):
        return None
    cutover = probe_evidence.get("cutover")
    if not isinstance(cutover, dict):
        return None
    evidence = cutover.get("evidence")
    return evidence if isinstance(evidence, dict) else None


__all__ = [
    "cutover_failure_profile",
    "persisted_cutover_evidence",
    "post_commit_cutover_failure_profile",
    "probe_outcome",
]
