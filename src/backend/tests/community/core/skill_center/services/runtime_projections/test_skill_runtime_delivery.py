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
    MappingApplyResult,
    MappingItemResult,
    MappingProjectionStatus,
    PoolSkillMapping,
    RegisteredSkillAsset,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.ports import LegacyMappingApplyRequired
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

    async def apply_mappings(self, **_kwargs):
        raise LegacyMappingApplyRequired()


class _MissingLayoutRepository:
    def get(self, _scope):
        return None


class _RecordingPoolRuntime:
    def __init__(self, *, fallback: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fallback = fallback

    async def apply_mappings(self, **kwargs):
        self.calls.append(("apply", kwargs))
        if self.fallback:
            raise LegacyMappingApplyRequired()
        mappings = [*kwargs["mappings"]]
        retired = [*kwargs["retired_mappings"]]
        return MappingApplyResult(
            status=MappingProjectionStatus.CONVERGED,
            items=tuple(
                MappingItemResult(
                    target="",
                    source=None,
                    status=MappingProjectionStatus.CONVERGED,
                    mapping=mapping,
                )
                for mapping in mappings
            )
            + tuple(
                MappingItemResult(
                    target="",
                    source=None,
                    status=MappingProjectionStatus.CONVERGED,
                    action="RETIRE",
                    mapping=mapping,
                )
                for mapping in retired
                if mapping.link_name not in {item.link_name for item in mappings}
            ),
        )

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


class _Factory:
    def __init__(self, service: _LegacyRuntimeService) -> None:
        self.service = service
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.service


def _plan(
    *assets: RegisteredSkillAsset,
) -> ResolvedSkillPlan:
    return ResolvedSkillPlan(
        bot_id="bot-1",
        owner_id="owner-1",
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
    plan = _plan(asset)
    factory = _Factory(service)
    delivery = SkillRuntimeDelivery(
        pool_runtime=_UnusedPoolRuntime(),
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(
        plan=plan, retired_mappings=(), service_factory=factory
    )

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
    assert len(factory.calls) == 1


@pytest.mark.asyncio
async def test_legacy_empty_delivery_keeps_device_sync_cleanup_route() -> None:
    service = _LegacyRuntimeService()
    delivery = SkillRuntimeDelivery(
        pool_runtime=_UnusedPoolRuntime(),
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(plan=_plan(), service_factory=_Factory(service))

    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert service.desired_skills == []


@pytest.mark.asyncio
async def test_repo_delivery_uses_one_logical_apply_call() -> None:
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
    factory = _Factory(service)

    result = await delivery.deliver(
        plan=_plan(asset), service_factory=factory
    )

    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert [name for name, _ in pool.calls] == ["apply"]
    for _, request in pool.calls:
        assert request["bot_id"] == "bot-1"
        assert request["user_id"] == "owner-1"
        assert request["source_layout"] is SkillMappingSourceLayout.LEGACY
        assert request["retired_mappings"] == []
    assert service.desired_skills is None
    assert factory.calls == []


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
    delivery = SkillRuntimeDelivery(
        pool_runtime=pool,
        pool_layouts=layouts,
    )

    await delivery.deliver(plan=_plan(asset), service_factory=_Factory(service))

    assert layouts.scopes == [scope]
    assert [name for name, _ in pool.calls] == ["apply"]
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
        plan=_plan(),
        retired_mappings=(retired,),
        service_factory=_Factory(service),
    )

    assert [name for name, _ in pool.calls] == ["apply"]
    for _, request in pool.calls:
        assert request["mappings"] == []
        assert request["retired_mappings"] == [retired]
        assert request["source_layout"] is SkillMappingSourceLayout.LEGACY
    assert service.desired_skills is None


@pytest.mark.asyncio
async def test_verified_old_runtime_keeps_legacy_mapping_route() -> None:
    service = _LegacyRuntimeService()
    pool = _RecordingPoolRuntime(fallback=True)
    asset = RegisteredSkillAsset(
        skill_id=8,
        name="repo-skill",
        git_path="git://team/repo-skill",
    )
    factory = _Factory(service)
    delivery = SkillRuntimeDelivery(
        pool_runtime=pool,
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(plan=_plan(asset), service_factory=factory)

    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert [name for name, _ in pool.calls] == ["apply", "publish", "verify"]
    assert factory.calls == []


@pytest.mark.asyncio
async def test_missing_logical_item_cannot_report_converged() -> None:
    service = _LegacyRuntimeService()
    pool = _RecordingPoolRuntime()

    async def incomplete(**kwargs):
        pool.calls.append(("apply", kwargs))
        return MappingApplyResult(status=MappingProjectionStatus.CONVERGED)

    pool.apply_mappings = incomplete
    asset = RegisteredSkillAsset(
        skill_id=8,
        name="repo-skill",
        git_path="git://team/repo-skill",
    )
    delivery = SkillRuntimeDelivery(
        pool_runtime=pool,
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(
        plan=_plan(asset), service_factory=_Factory(service)
    )

    assert result.status is RuntimeProjectionStatus.DEGRADED
    assert [issue.code for issue in result.issues] == [
        "SKILL_MAPPING_RESULT_INVALID"
    ]


@pytest.mark.asyncio
async def test_aggregate_degraded_status_survives_empty_items() -> None:
    service = _LegacyRuntimeService()
    pool = _RecordingPoolRuntime()

    async def aggregate_failure(**kwargs):
        pool.calls.append(("apply", kwargs))
        return MappingApplyResult(status=MappingProjectionStatus.DEGRADED)

    pool.apply_mappings = aggregate_failure
    delivery = SkillRuntimeDelivery(
        pool_runtime=pool,
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(
        plan=_plan(),
        service_factory=_Factory(service),
    )

    assert result.status is RuntimeProjectionStatus.DEGRADED
    assert result.issues[0].code == "SKILL_MAPPING_RUNTIME_UNAVAILABLE"


@pytest.mark.asyncio
async def test_logical_issue_preserves_requested_action_and_skill_identity() -> None:
    service = _LegacyRuntimeService()
    pool = _RecordingPoolRuntime()
    asset = RegisteredSkillAsset(
        skill_id=8,
        name="runtime-name",
        git_path="git://team/package-name",
    )
    mapping = RuntimeProjectionResolver().resolve_skills((asset,)).skill_mappings[0]

    async def pending(**kwargs):
        pool.calls.append(("apply", kwargs))
        return MappingApplyResult(
            status=MappingProjectionStatus.PENDING,
            items=(
                MappingItemResult(
                    target="/diagnostic/not-the-skill-name",
                    source="/diagnostic/package-name",
                    status=MappingProjectionStatus.PENDING,
                    code="MANAGED_SOURCE_MISSING",
                    retryable=True,
                    action="APPLY",
                    mapping=mapping,
                ),
            ),
        )

    pool.apply_mappings = pending
    delivery = SkillRuntimeDelivery(
        pool_runtime=pool,
        pool_layouts=_MissingLayoutRepository(),
    )

    result = await delivery.deliver(
        plan=_plan(asset),
        service_factory=_Factory(service),
    )

    assert result.issues[0].resource_id == "8"
    assert result.issues[0].name == "runtime-name"
    assert result.issues[0].requested_action == "APPLY"
