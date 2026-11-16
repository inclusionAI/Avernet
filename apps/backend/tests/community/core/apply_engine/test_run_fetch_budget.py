"""Fetch allowances observed through ApplyRunService's producer boundary."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.apply_engine import fetch_budget
from agentclaw.community.core.apply_engine.compiler import Compiled, StateRef
from agentclaw.community.core.apply_engine.desired_state import Declaration, DesiredScript, DesiredState
from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
from agentclaw.community.core.apply_engine.constructs import Section
from agentclaw.community.core.apply_engine.run.service import ApplyRunService
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)
from tests.community.core.bot_config_manifest.compiler.test_compile import _compiler
from ._fakes import delivery_strategies, device_port_bundle, FakeStartupScriptService
from .test_apply_completion import _DB, _Bots, _Capabilities, _Queue, _BOT_RECORD

_STATE = StateRef("budget-producer")


class _Producer:
    """A registered state producer, exercising the execution allowance it receives."""

    def __init__(self, compile):
        self.compile = compile

    def check(self, ctx, ref):
        pass


class _ScriptDelivery(ArcaDelivery):
    """A script-only family: isolates lifecycle from other constructs' contracts."""

    def steps_for(self, phase=None):
        return tuple(step for step in super().steps_for(phase) if step.construct is Section.SCRIPT)


def _service(compile, *, content=None, scripts=None):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    db = _DB(engine)
    queue = _Queue()
    service = ApplyRunService(
        capabilities=_Capabilities(),
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        compilers={_STATE.kind: _Producer(compile)},
        completion_handlers={},
        task_queue_provider=lambda: queue,
        bot_repository=_Bots(_BOT_RECORD),
        delivery_strategies=delivery_strategies(
            arca=_ScriptDelivery(device_port_bundle(content=content, script_service=scripts))
        ),
    )
    return service, queue


async def _dry_run(service):
    return await service.dry_run(
        entity_id="u_owner", bot_id="b_1", bot=_BOT_RECORD,
        owner_id="u_owner", actor_id="u_actor", state=_STATE,
    )


@pytest.mark.parametrize("dry_run", [True, False])
def test_manifest_fetches_spend_the_execution_allowance(dry_run, monkeypatch):
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", 4)
    compiler, content = _compiler(
        """schema_version: 1
manifest:
  resources:
    - path: data/review.txt
      source: {protocol: oss, bucket: manifest-fixtures, key: review.txt, auth: oss-cred}
""",
        {"review.txt": b"data"},
    )
    observations = []

    async def compile(ctx, ref, *, only=None):
        assert fetch_budget.current_fetch_budget().expired() is None
        result = await compiler.compile(ctx, StateRef("manifest"), only=only)
        observations.append(fetch_budget.current_fetch_budget().expired())
        return result

    service, queue = _service(compile, content=content)
    if dry_run:
        report = asyncio.run(_dry_run(service))
    else:
        service.start_apply(
            entity_id="u_owner", bot_id="b_1", bot=_BOT_RECORD,
            owner_id="u_owner", actor_id="u_actor", state=_STATE,
        )
        service.run_apply_task(queue.payloads[-1])
        report = service.last_apply(entity_id="u_owner", bot_id="b_1")
    assert report.status is ApplyStatus.SUCCEEDED
    assert len(observations) == 1
    assert "fetch budget is exhausted (bytes)" in observations[0]
    assert fetch_budget.current_fetch_budget() is None


@pytest.mark.asyncio
async def test_nested_failed_preview_restores_its_parent_and_thread_fetches_share_the_ledger(monkeypatch):
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", 4)
    observed = []

    async def compile(ctx, ref, *, only=None):
        budget = fetch_budget.current_fetch_budget()
        observed.append(budget.expired())
        if ref.reference == "child":
            raise ValueError("producer unavailable")

        def fetch():
            fetch_budget.current_fetch_budget().charge(4)

        await asyncio.to_thread(fetch)
        with pytest.raises(ValueError, match="producer unavailable"):
            await service.dry_run(
                entity_id="u_owner", bot_id="b_1", bot=_BOT_RECORD,
                owner_id="u_owner", actor_id="u_actor", state=StateRef(_STATE.kind, "child"),
            )
        observed.append(fetch_budget.current_fetch_budget().expired())
        return Compiled(DesiredState.EMPTY)

    service, _ = _service(compile)
    await _dry_run(service)
    assert observed[:2] == [None, None], "the nested execution starts with its own allowance"
    assert "fetch budget is exhausted (bytes)" in observed[2]
    assert fetch_budget.current_fetch_budget() is None


@pytest.mark.asyncio
async def test_concurrent_previews_and_later_retry_have_independent_allowances(monkeypatch):
    monkeypatch.setattr(fetch_budget, "APPLY_FETCH_TOTAL_LIMIT", 4)
    entered = asyncio.Event()
    release = asyncio.Event()
    observed = []

    async def compile(ctx, ref, *, only=None):
        budget = fetch_budget.current_fetch_budget()
        observed.append(budget.expired())
        if not entered.is_set():
            budget.charge(4)
            entered.set()
            await release.wait()
        else:
            release.set()
        return Compiled(DesiredState.EMPTY)

    service, _ = _service(compile)
    first = asyncio.create_task(_dry_run(service))
    await entered.wait()
    await _dry_run(service)
    await first
    await _dry_run(service)
    assert observed == [None, None, None]
    assert fetch_budget.current_fetch_budget() is None


@pytest.mark.asyncio
async def test_failed_compile_does_not_leave_a_ledger_in_its_callers_context():
    async def compile(ctx, ref, *, only=None):
        assert fetch_budget.current_fetch_budget() is not None
        raise ValueError("invalid state")

    service, _ = _service(compile)
    with pytest.raises(ValueError, match="invalid state"):
        await _dry_run(service)
    assert fetch_budget.current_fetch_budget() is None


@pytest.mark.asyncio
async def test_compile_and_category_plan_use_the_same_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(fetch_budget, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(fetch_budget, "APPLY_BUDGET_S", 3.0)
    observations = []

    class Scripts(FakeStartupScriptService):
        def get_body(self, **kwargs):
            observations.append(fetch_budget.current_fetch_budget().expired())
            return super().get_body(**kwargs)

    async def compile(ctx, ref, *, only=None):
        assert fetch_budget.current_fetch_budget().expired() is None
        now[0] = 103.0
        return Compiled(DesiredState(script=Declaration((DesiredScript("echo ready"),))))

    service, _ = _service(compile, scripts=Scripts())
    await _dry_run(service)
    assert len(observations) == 1
    assert "fetch budget is exhausted (time)" in observations[0]
    assert fetch_budget.current_fetch_budget() is None
