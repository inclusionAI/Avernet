from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
    runtime_uses_pool_paths,
)


def _state(
    *,
    active_layout: SkillLayout = SkillLayout.LEGACY,
    phase: SkillLayoutPhase = SkillLayoutPhase.LEGACY_ACTIVE,
    data_plane_cutover_committed: bool = False,
) -> BotSkillLayoutState:
    return BotSkillLayoutState(
        scope=BotSkillLayoutScope(env="pre", entity_id="staff_1", bot_id="bot-1"),
        active_layout=active_layout,
        target_layout=(
            SkillLayout.POOL
            if active_layout is SkillLayout.LEGACY
            else None
        ),
        phase=phase,
        migration_generation="generation-1",
        persisted=True,
        data_plane_cutover_committed=data_plane_cutover_committed,
    )


def test_pool_paths_become_authoritative_during_cutover_finalizing() -> None:
    assert runtime_uses_pool_paths(
        _state(phase=SkillLayoutPhase.POOL_CUTOVER_FINALIZING)
    )


def test_pool_paths_remain_authoritative_after_data_plane_commit() -> None:
    assert runtime_uses_pool_paths(
        _state(
            phase=SkillLayoutPhase.NEEDS_MANUAL_REPAIR,
            data_plane_cutover_committed=True,
        )
    )


def test_begin_cutover_fences_backend_consumers_to_pool_paths() -> None:
    assert runtime_uses_pool_paths(
        _state(phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER)
    )


def test_pool_paths_are_not_used_while_only_preparing() -> None:
    assert not runtime_uses_pool_paths(
        _state(phase=SkillLayoutPhase.POOL_READY)
    )
