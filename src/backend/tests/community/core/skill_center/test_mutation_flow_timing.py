from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from agentclaw.community.core.repository.capability_desired_state_types import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.core.skill_center.services._mutation_flow import (
    MutationProjectionFlow,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)


class _Repository:
    def restore_desired_state(self, **_kwargs) -> None:
        raise AssertionError("restore is not expected on the success path")


class _Runtime:
    def __init__(self) -> None:
        self.plan = SimpleNamespace(
            projection=SimpleNamespace(skill_mappings=())
        )

    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def project(self, **_kwargs) -> None:
        return None

    def resolve_plan(self, **_kwargs):
        return self.plan

    async def apply_plan(self, **_kwargs) -> None:
        return None


class _PlanRuntime:
    def __init__(self) -> None:
        self.before = (
            PoolSkillMapping(corpus="local", relative_path="old", link_name="old"),
        )
        self.after = (
            PoolSkillMapping(corpus="repo", relative_path="new", link_name="new"),
        )
        self.plan = SimpleNamespace(
            projection=SimpleNamespace(skill_mappings=self.after)
        )
        self.snapshot_calls = 0
        self.resolve_calls = 0
        self.applied_plan = None
        self.retired_mappings = ()
        self.fail_resolve = False
        self.fail_apply_once = False

    async def snapshot_skill_mappings(self, **_kwargs):
        self.snapshot_calls += 1
        return self.before

    def resolve_plan(self, **_kwargs):
        self.resolve_calls += 1
        if self.fail_resolve:
            raise RuntimeError("plan unavailable")
        return self.plan

    async def apply_plan(self, *, plan, retired_mappings, **_kwargs):
        self.applied_plan = plan
        self.retired_mappings = tuple(retired_mappings)
        if self.fail_apply_once:
            self.fail_apply_once = False
            raise RuntimeError("runtime unavailable")
        return RuntimeProjectionResult.converged()


@pytest.mark.asyncio
async def test_mutation_flow_logs_control_plane_timing_stages(caplog) -> None:
    flow = MutationProjectionFlow(repository=_Repository(), runtime=_Runtime())
    caplog.set_level(logging.INFO)

    result = await flow.apply(
        bot={"owner_id": "owner-1", "status": "ACTIVE"},
        bot_id="bot-1",
        engine_type="openclaw",
        scope=ProjectionScope(skills=True),
        mutation=lambda: DesiredStateMutation(
            item={"id": "set-1"},
            changed=True,
            previous_state=CapabilityDesiredState(
                installations=set(),
                set_active={},
                memberships={},
            ),
        ),
    )

    assert result == {
        "id": "set-1",
        "changed": True,
        "runtime_projection": {
            "status": "CONVERGED",
            "components": {},
            "pending_count": 0,
            "degraded_count": 0,
            "issues": [],
        },
    }
    messages = [record.getMessage() for record in caplog.records]
    for stage in (
        "snapshot_before",
        "desired_state_mutation",
        "snapshot_after",
        "runtime_projection",
    ):
        assert any(
            "[MutationProjectionFlow] timing" in message
            and f"stage={stage}" in message
            and "bot_id=bot-1" in message
            and "duration_ms=" in message
            for message in messages
        )


@pytest.mark.asyncio
async def test_mutation_flow_applies_the_single_post_mutation_plan() -> None:
    runtime = _PlanRuntime()
    flow = MutationProjectionFlow(repository=_Repository(), runtime=runtime)

    await flow.apply(
        bot={"owner_id": "owner-1", "status": "ACTIVE"},
        bot_id="bot-1",
        engine_type="openclaw",
        scope=ProjectionScope(skills=True),
        mutation=lambda: DesiredStateMutation(
            item={"id": "set-1"},
            changed=True,
            previous_state=CapabilityDesiredState(
                installations=set(),
                set_active={},
                memberships={},
            ),
        ),
    )

    assert runtime.snapshot_calls == 1
    assert runtime.resolve_calls == 1
    assert runtime.applied_plan is runtime.plan
    assert runtime.retired_mappings == runtime.before


@pytest.mark.asyncio
async def test_mutation_flow_preserves_plan_failure_and_retry_outcomes() -> None:
    runtime = _PlanRuntime()
    flow = MutationProjectionFlow(repository=_Repository(), runtime=runtime)
    mutation = lambda: DesiredStateMutation(
        item={"id": "set-1"},
        changed=True,
        previous_state=CapabilityDesiredState(
            installations=set(), set_active={}, memberships={}
        ),
    )

    runtime.fail_resolve = True
    unresolved = await flow.apply(
        bot={"owner_id": "owner-1", "status": "ACTIVE"},
        bot_id="bot-1",
        engine_type="openclaw",
        scope=ProjectionScope(skills=True),
        mutation=mutation,
    )
    runtime.fail_resolve = False
    runtime.fail_apply_once = True
    unavailable = await flow.apply(
        bot={"owner_id": "owner-1", "status": "ACTIVE"},
        bot_id="bot-1",
        engine_type="openclaw",
        scope=ProjectionScope(skills=True),
        mutation=mutation,
    )
    retried = await flow.apply(
        bot={"owner_id": "owner-1", "status": "ACTIVE"},
        bot_id="bot-1",
        engine_type="openclaw",
        scope=ProjectionScope(skills=True),
        mutation=mutation,
    )

    assert unresolved["runtime_projection"]["issues"][0]["code"] == "RUNTIME_SNAPSHOT_UNAVAILABLE"
    assert unavailable["runtime_projection"]["issues"][0]["code"] == "RUNTIME_PROJECTION_UNAVAILABLE"
    assert retried["runtime_projection"]["status"] == "CONVERGED"
    assert runtime.resolve_calls == 3


@pytest.mark.asyncio
async def test_apply_plan_validates_retired_mappings_before_runtime_write() -> None:
    class _Engine:
        def __init__(self) -> None:
            self.validated_retired = ()

        def validate_plan(self, *, retired_mappings=(), **_kwargs) -> None:
            self.validated_retired = tuple(retired_mappings)

        async def apply(self, **_kwargs):
            return RuntimeProjectionResult.converged()

    class _Registry:
        def __init__(self, engine) -> None:
            self.engine = engine

        def for_engine(self, _name):
            return self.engine

    engine = _Engine()
    projector = object.__new__(BotRuntimeProjector)
    projector._registry = _Registry(engine)
    plan = SimpleNamespace(
        engine="openclaw",
        bot_id="bot-1",
        projection=SimpleNamespace(skill_assets=()),
    )
    retired = (PoolSkillMapping(corpus="local", relative_path="old", link_name="old"),)

    await projector.apply_plan(
        plan=plan,
        retired_mappings=retired,
        scope=ProjectionScope(skills=True),
    )

    assert engine.validated_retired == retired
