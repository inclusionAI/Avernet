"""Behaviour contract for steady-state filesystem Skill delivery."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ResolvedSkillPlan,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skill_center.services.runtime_projections.skill_runtime_delivery import (
    SkillRuntimeDelivery,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


class _LegacyRuntimeService:
    def __init__(self) -> None:
        self.desired_skills: list[dict[str, str | None]] | None = None

    async def project_skills(
        self,
        *,
        desired_skills: list[dict[str, str | None]],
        effective_mcps: list[dict] | None = None,
    ) -> bool:
        assert effective_mcps is None
        self.desired_skills = desired_skills
        return True


class _UnusedPoolRuntime:
    async def probe(self, **_kwargs):
        raise AssertionError("Legacy Local-only delivery must not probe")

    async def publish_mappings(self, **_kwargs):
        raise AssertionError("Legacy Local-only delivery must not publish mappings")

    async def verify_mappings(self, **_kwargs):
        raise AssertionError("Legacy Local-only delivery must not verify mappings")


class _MissingLayoutRepository:
    def get(self, _scope):
        return None


class _RecordingPoolRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def probe(self, **kwargs):
        self.calls.append(("probe", kwargs))
        return type("Probe", (), {"evidence": {}})()

    async def publish_mappings(self, **kwargs):
        self.calls.append(("publish", kwargs))
        return True

    async def verify_mappings(self, **kwargs):
        self.calls.append(("verify", kwargs))
        return True


class _LayoutRepository:
    def __init__(self, state: BotSkillLayoutState) -> None:
        self.state = state
        self.scopes: list[BotSkillLayoutScope] = []

    def get(self, scope: BotSkillLayoutScope):
        self.scopes.append(scope)
        return self.state


def _plan(
    service: _LegacyRuntimeService,
    *assets: RegisteredSkillAsset,
) -> ResolvedSkillPlan:
    return ResolvedSkillPlan(
        bot_id="bot-1",
        owner_id="owner-1",
        service=service,
        bot={
            "env": "pre",
            "entity_id": "owner-1",
            "active_engine": "openclaw",
        },
        engine="openclaw",
        projection=RuntimeProjectionResolver().resolve_skills(tuple(assets)),
    )


@pytest.mark.asyncio
async def test_legacy_local_only_delivery_keeps_device_sync_request_and_result() -> (
    None
):
    service = _LegacyRuntimeService()
    asset = RegisteredSkillAsset(
        skill_id=7,
        name="local-skill",
        git_path="local:///home/admin/.openclaw/workspace/skills/skills-local/local-skill",
    )
    plan = _plan(service, asset)
    delivery = SkillRuntimeDelivery(
        pool_runtime=_UnusedPoolRuntime(),
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(plan=plan, retired_mappings=())

    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert result.components == {"skills": RuntimeProjectionStatus.CONVERGED}
    assert service.desired_skills == [
        {
            "id": "7",
            "name": "local-skill",
            "git_path": asset.git_path,
            "skill_uuid": None,
            "sc_version_number": None,
        }
    ]


@pytest.mark.asyncio
async def test_legacy_empty_delivery_keeps_device_sync_cleanup_route() -> None:
    service = _LegacyRuntimeService()
    delivery = SkillRuntimeDelivery(
        pool_runtime=_UnusedPoolRuntime(),
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(plan=_plan(service))

    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert service.desired_skills == []


@pytest.mark.asyncio
async def test_repo_delivery_keeps_legacy_mapping_wire_and_order() -> None:
    service = _LegacyRuntimeService()
    pool = _RecordingPoolRuntime()
    asset = RegisteredSkillAsset(
        skill_id=8,
        name="repo-skill",
        git_path="git://team/repo-skill",
    )
    delivery = SkillRuntimeDelivery(
        pool_runtime=pool,
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(plan=_plan(service, asset))

    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert [name for name, _ in pool.calls] == ["publish", "verify"]
    for _, request in pool.calls:
        assert request["bot_id"] == "bot-1"
        assert request["user_id"] == "owner-1"
        assert request["source_layout"] is SkillMappingSourceLayout.LEGACY
        assert request["mapping_contract_version"] == "skills-pool-mapping-v2"
        assert request["retired_mappings"] == []
    assert service.desired_skills is None


@pytest.mark.asyncio
async def test_cutover_intermediate_state_uses_pool_mapping_sources() -> None:
    service = _LegacyRuntimeService()
    pool = _RecordingPoolRuntime()
    scope = BotSkillLayoutScope(env="pre", entity_id="owner-1", bot_id="bot-1")
    layouts = _LayoutRepository(
        BotSkillLayoutState(
            scope=scope,
            active_layout=SkillLayout.LEGACY,
            target_layout=SkillLayout.POOL,
            phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
            migration_generation="generation-1",
            persisted=True,
        )
    )
    asset = RegisteredSkillAsset(
        skill_id=7,
        name="local-skill",
        git_path="local:///home/admin/.openclaw/workspace/skills/skills-local/local-skill",
    )
    delivery = SkillRuntimeDelivery(pool_runtime=pool, pool_layouts=layouts)

    await delivery.deliver(plan=_plan(service, asset))

    assert layouts.scopes == [scope]
    assert [name for name, _ in pool.calls] == ["publish", "verify"]
    assert all(
        request["source_layout"] is SkillMappingSourceLayout.POOL
        for _, request in pool.calls
    )
    assert service.desired_skills is None


@pytest.mark.asyncio
async def test_explicit_retirement_uses_mapping_route_for_empty_legacy_plan() -> None:
    service = _LegacyRuntimeService()
    pool = _RecordingPoolRuntime()
    retired = PoolSkillMapping(
        corpus="local",
        relative_path="retired-skill",
        link_name="retired-skill",
    )
    delivery = SkillRuntimeDelivery(
        pool_runtime=pool,
        pool_layouts=_MissingLayoutRepository(),
    )

    await delivery.deliver(
        plan=_plan(service),
        retired_mappings=(retired,),
    )

    assert [name for name, _ in pool.calls] == ["publish", "verify"]
    for _, request in pool.calls:
        assert request["mappings"] == []
        assert request["retired_mappings"] == [retired]
        assert request["source_layout"] is SkillMappingSourceLayout.LEGACY
    assert service.desired_skills is None
