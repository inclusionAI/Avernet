"""AICoding active-root corpus retirement resume policy."""

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutState,
    SkillLayoutPhase,
)


def is_trusted_aicoding_repo_retirement_resume(
    *,
    state: BotSkillLayoutState,
    engine: str,
    probe: RuntimeLayoutProbeResult,
) -> bool:
    """Allow only the known, committed AICoding finalization to re-enter."""

    return (
        state.phase is SkillLayoutPhase.POOL_CUTOVER_FINALIZING
        and state.data_plane_cutover_committed
        and probe.status is RuntimeLayoutProbeStatus.INVALID
        and probe.engine == engine
        and probe.layout_contract_version == state.layout_contract_version
        and probe.preparation_id == state.preparation_id
        and probe.evidence.get("reason") == "active_repo_corpus_present"
        and probe.evidence.get("implementation_engine") == "aicoding"
        and probe.evidence.get("physical_layout_engine") == "aicoding"
    )


def is_trusted_aicoding_repo_restoration_resume(
    *,
    state: BotSkillLayoutState,
    engine: str,
    probe: RuntimeLayoutProbeResult,
) -> bool:
    """Allow an identity-matched Legacy rollback to finish after restoration."""

    return (
        state.phase is SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING
        and probe.status is RuntimeLayoutProbeStatus.INVALID
        and probe.engine == engine
        and probe.layout_contract_version == state.layout_contract_version
        and probe.preparation_id == state.preparation_id
        and probe.evidence.get("reason") == "active_repo_corpus_present"
        and probe.evidence.get("implementation_engine") == "aicoding"
        and probe.evidence.get("physical_layout_engine") == "aicoding"
    )


__all__ = [
    "is_trusted_aicoding_repo_restoration_resume",
    "is_trusted_aicoding_repo_retirement_resume",
]
