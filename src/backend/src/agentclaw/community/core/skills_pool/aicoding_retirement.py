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


def is_aicoding_active_mapping_reconciliation_candidate(
    *,
    state: BotSkillLayoutState,
    engine: str,
    probe: RuntimeLayoutProbeResult,
) -> bool:
    """Allow one idempotent mapping republish for the retired repo bridge form.

    The old AICoding migration wrote active repo Skills through the stable
    ``~/.aicoding/skills-repo`` bridge.  New probes deliberately reject that
    indirect target, but the current Pool mapping publisher can rewrite every
    *currently managed* entry to its direct canonical Pool source.  This is
    not a general INVALID bypass: the Engine remains the authority for the
    mapping publish and the caller must probe again before reporting READY.
    """

    return (
        state.phase is SkillLayoutPhase.POOL_ACTIVE
        and state.data_plane_cutover_committed
        and state.preparation_id is not None
        and probe.status is RuntimeLayoutProbeStatus.INVALID
        and probe.engine == engine
        and probe.layout_contract_version == state.layout_contract_version
        and probe.preparation_id == state.preparation_id
        and probe.evidence.get("reason") == "active_managed_entry_invalid"
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
    "is_aicoding_active_mapping_reconciliation_candidate",
    "is_trusted_aicoding_repo_restoration_resume",
    "is_trusted_aicoding_repo_retirement_resume",
]
