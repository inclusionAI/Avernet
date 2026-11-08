"""Per-run max_attempts override tests.

A run whose ``run_options.max_attempts`` is set caps that run's subagent
retry budget; absent it falls to the pod's configured default. The value
is snapshotted at spawn, so every attempt of an item sees one number.
A transient (retryable) error is used so the attempt count is observable:
the budget is what terminates the loop.
"""
from __future__ import annotations

import asyncio
import uuid

from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, EpochMillis, ProviderKind, RequestMetadata, Run, RunId,
    RunOptions, RunStatus, SubagentResult, TaskEnvelope, TenantId,
)
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime

import time


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(options: RunOptions | None) -> Run:
    return Run(
        run_id=RunId("run-" + uuid.uuid4().hex[:8]),
        tenant=TenantId("tenant-a"),
        goal="attempts goal",
        agent_catalog=(AgentSpec(name="scout", role="worker"),),
        provider=ProviderKind("stub"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
        run_options=options,
    )


def _mk_envelope() -> TaskEnvelope:
    return TaskEnvelope(
        name="scout", objective="do the thing", output_schema={}, boundaries="",
        parent_goal="attempts goal",
    )


class _TransientSubagent:
    def __init__(self, spec: AgentSpec) -> None:
        self.name = spec.name
        self.executor_id = "ac_x"
        self.executor_name = spec.name

    async def setup(self) -> None:
        raise RuntimeError("transient boom")

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        raise AssertionError("run_task must never run when setup fails")

    async def teardown(self) -> None:
        pass

    async def halt(self) -> None:
        pass


class _TransientFactory:
    def __init__(self) -> None:
        self.creates = 0

    def create(self, spec, run, agent_id=None, attempt=1):
        self.creates += 1
        return _TransientSubagent(spec)


def _run_once(run: Run, boot_max: int):
    async def main():
        factory = _TransientFactory()
        rt = SubagentRuntime(factory=factory, max_attempts=boot_max)
        await rt.start()
        try:
            await rt.spawn(run, _mk_envelope())
            results = await rt.wait_for_completions()
        finally:
            await rt.stop()
        return factory, results

    return asyncio.run(main())


class TestPerRunMaxAttempts:

    def test_override_one_caps_attempts_at_one(self):
        factory, (r,) = _run_once(_mk_run(RunOptions(max_attempts=1)), boot_max=3)
        assert r["status"].value == "FAILED"
        assert r["attempts"] == 1
        assert factory.creates == 1

    def test_override_two_burns_two(self):
        factory, (r,) = _run_once(_mk_run(RunOptions(max_attempts=2)), boot_max=3)
        assert r["attempts"] == 2
        assert factory.creates == 2

    def test_absent_override_uses_boot_default(self):
        factory, (r,) = _run_once(_mk_run(RunOptions()), boot_max=3)
        assert r["attempts"] == 3
        assert factory.creates == 3

    def test_none_run_options_uses_boot_default(self):
        factory, (r,) = _run_once(_mk_run(None), boot_max=3)
        assert r["attempts"] == 3
        assert factory.creates == 3

    def test_override_clamped_to_boot_cap(self):
        factory, (r,) = _run_once(_mk_run(RunOptions(max_attempts=9)), boot_max=2)
        assert r["attempts"] == 2
        assert factory.creates == 2