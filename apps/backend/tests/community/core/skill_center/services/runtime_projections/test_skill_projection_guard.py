"""Stale or concurrent snapshots cannot authorize shared-link deletion."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError, DeviceConnectionUnavailableError, DeviceContext, DeviceOfflineError,
)
from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import PerDomainRuntimeProjection
from agentclaw.community.core.skill_center.services.runtime_projections.skill_runtime_delivery import SkillRuntimeDelivery
from agentclaw.community.core.skill_center.services.runtime_projections.skill_projection_guard import SkillProjectionGuard
from agentclaw.community.core.skill_center.runtime_projection_contract import ProjectionScope, ResolvedSkillPlan, RuntimeProjectionResult, RuntimeProjectionStatus
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeProjectionResolver
from agentclaw.community.core.skills_pool.models import MappingApplyResult, MappingProjectionStatus, RegisteredSkillAsset
from agentclaw.community.plugins.local.cache import MemoryCachePlugin
from agentclaw.community.plugin_api.cache import CacheLockInfrastructureError


def setup_guard():
    bot = {"env": "pre", "active_engine": "openclaw", "entity_id": "owner", "binding_id": 1}
    state = SimpleNamespace(assets=(), bot=bot)
    reader = SimpleNamespace(active_skill_assets=lambda **_: state.assets)
    bots = SimpleNamespace(get_by_id_and_owner=lambda *_: state.bot)
    cache = MemoryCachePlugin()
    guard = SkillProjectionGuard(cache=cache, reader=reader, bots=bots)
    plan = ResolvedSkillPlan(
        bot_id="bot", owner_id="owner", bot=bot, engine="openclaw",
        projection=RuntimeProjectionResolver().resolve_skills(()),
    )
    return state, cache, guard, plan, reader, bots


@pytest.mark.asyncio
async def test_new_direct_projection_wins_over_delayed_old_snapshot():
    state, _, guard, old, _, _ = setup_guard()
    state.assets = (RegisteredSkillAsset(
        skill_id=1, name="direct", git_path="git://infra/direct",
    ),)
    new = replace(old, projection=RuntimeProjectionResolver().resolve_skills(state.assets))
    send = AsyncMock(return_value=RuntimeProjectionResult.converged())
    assert (await guard.deliver(plan=new, send=send)).status is RuntimeProjectionStatus.CONVERGED
    assert (await guard.deliver(plan=old, send=send)).issues[0].code == "SKILL_PROJECTION_STALE"
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_two_backend_instances_share_the_same_bot_lease():
    _, cache, first, plan, reader, bots = setup_guard()
    second = SkillProjectionGuard(cache=cache, reader=reader, bots=bots)
    started, finish = asyncio.Event(), asyncio.Event()

    async def blocked():
        started.set()
        await finish.wait()
        return RuntimeProjectionResult.converged()

    task = asyncio.create_task(first.deliver(plan=plan, send=blocked))
    await started.wait()
    send = AsyncMock(return_value=RuntimeProjectionResult.converged())
    assert (await second.deliver(plan=plan, send=send)).issues[0].code == "SKILL_PROJECTION_BUSY"
    send.assert_not_awaited()
    finish.set()
    await task
    assert (await second.deliver(plan=plan, send=send)).status is RuntimeProjectionStatus.CONVERGED


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reader", "cache", "identity"])
async def test_failed_reads_locks_or_changed_runtime_never_send(failure, monkeypatch):
    state, cache, guard, plan, reader, _ = setup_guard()

    def unavailable(*_, **__):
        raise CacheLockInfrastructureError("unavailable")

    if failure == "cache":
        monkeypatch.setattr(cache, "acquire_lock_strict", unavailable)
    elif failure == "reader":
        reader.active_skill_assets = unavailable
    else:
        state.bot = {**state.bot, "binding_id": 2}
    send = AsyncMock()
    assert (await guard.deliver(plan=plan, send=send)).status is RuntimeProjectionStatus.PENDING
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [
    {"template_type": "coding"},
    {"template_config": {"engine_form": "aicoding"}},
])
@pytest.mark.parametrize("reverse", [False, True])
async def test_changed_physical_runtime_rejects_old_plan(template, reverse):
    state, _, guard, plan, _, _ = setup_guard()
    claude = {**state.bot, "active_engine": "claude_code", "template_type": "normalCC"}
    aicoding = {**claude, **template}
    old, current = (aicoding, claude) if reverse else (claude, aicoding)
    plan = replace(plan, bot=old, engine="claude_code")
    state.bot = current
    send = AsyncMock()

    result = await guard.deliver(plan=plan, send=send)

    assert result.issues[0].code == "SKILL_PROJECTION_STALE"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_template_change_with_same_physical_runtime_can_deliver():
    state, _, guard, plan, _, _ = setup_guard()
    bot = {**state.bot, "active_engine": "claude_code", "template_type": "coding"}
    plan = replace(plan, bot=bot, engine="claude_code")
    state.bot = {**bot, "template_type": "another-coding-template"}
    send = AsyncMock(return_value=RuntimeProjectionResult.converged())

    assert (await guard.deliver(plan=plan, send=send)).status is RuntimeProjectionStatus.CONVERGED
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_timeout_keeps_lease_instead_of_admitting_overlap():
    _, _, guard, plan, _, _ = setup_guard()
    send = AsyncMock(side_effect=TimeoutError)
    assert (await guard.deliver(plan=plan, send=send)).issues[0].code == "SKILL_PROJECTION_TIMEOUT"
    assert (await guard.deliver(plan=plan, send=send)).issues[0].code == "SKILL_PROJECTION_BUSY"


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "code"), [
    (DeviceOfflineError, "DESKTOP_DEVICE_OFFLINE"),
    (DeviceConnectionUnavailableError, "SKILL_RUNTIME_UNAVAILABLE"),
    (ConnInfoBuildError, "SKILL_RUNTIME_UNAVAILABLE"),
])
async def test_connection_resolution_failure_releases_lease_for_immediate_recovery(failure, code):
    state, _, guard, plan, _, _ = setup_guard()
    state.bot = {**state.bot, "bot_type": "desktop"}
    plan = replace(plan, bot=state.bot)
    state.online = False

    def resolve(*_):
        if not state.online:
            raise failure("connection resolution failed before mapping send")
        return DeviceContext(
            provider="baas", conn_info={}, binding_id=1,
            bot_id="bot", user_id="owner", bot_type="desktop",
        )

    apply = AsyncMock(return_value=MappingApplyResult(status=MappingProjectionStatus.CONVERGED))
    projection = PerDomainRuntimeProjection(skill_delivery=SkillRuntimeDelivery(
        guard=guard, pool_runtime=SimpleNamespace(apply_mappings=apply),
        pool_layouts=SimpleNamespace(get=lambda _: None),
        device_contexts=SimpleNamespace(resolve_for_bot=resolve),
        center_content=SimpleNamespace(),
    ))
    kwargs = dict(plan=plan, scope=ProjectionScope(skills=True), service_factory=SimpleNamespace())

    offline = await projection.apply(**kwargs)
    assert offline.issues[0].code == code
    apply.assert_not_awaited()

    state.online = True
    assert (await projection.apply(**kwargs)).status is RuntimeProjectionStatus.CONVERGED
    apply.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_lease_loss_before_transport_is_zero_write(unavailable, monkeypatch):
    _, cache, guard, plan, _, _ = setup_guard()

    def renew(*_, **__):
        if unavailable:
            raise CacheLockInfrastructureError("unavailable")
        return False

    monkeypatch.setattr(cache, "renew_lock_strict", renew)
    send = AsyncMock()
    result = await guard.deliver(plan=plan, send=send)
    assert result.issues[0].code in {"SKILL_PROJECTION_LOCK_LOST", "SKILL_PROJECTION_LOCK_UNAVAILABLE"}
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("connection lost"), asyncio.CancelledError()])
async def test_interrupted_remote_call_remains_quarantined(error):
    _, _, guard, plan, _, _ = setup_guard()
    send = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await guard.deliver(plan=plan, send=send)
    assert (await guard.deliver(plan=plan, send=send)).issues[0].code == "SKILL_PROJECTION_BUSY"


@pytest.mark.asyncio
async def test_unknown_runtime_result_remains_quarantined():
    _, _, guard, plan, _, _ = setup_guard()
    send = AsyncMock(return_value=RuntimeProjectionResult.pending(
        code="SKILL_MAPPING_RUNTIME_UNAVAILABLE", reason="remote outcome unknown",
    ))
    await guard.deliver(plan=plan, send=send)
    assert (await guard.deliver(plan=plan, send=send)).issues[0].code == "SKILL_PROJECTION_BUSY"
