"""Non-retryable provisioning classification tests.

A ``NonRetryableProvisioningError`` raised from an agent's setup fails
the attempt on attempt 1 — deterministic recurrences (a space quota
ceiling) must not burn the attempt budget or feed the LLM upstream
breaker — while a plain retryable error keeps burning the full budget.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, EpochMillis, NonRetryableProvisioningError, ProviderKind,
    RequestMetadata, Run, RunId, RunStatus, SubagentResult, TaskEnvelope,
    TenantId,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    BreakerState, UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime

import time


def _now_ms():
    return int(time.time() * 1000)


def _mk_run() -> Run:
    return Run(
        run_id=RunId("run-" + uuid.uuid4().hex[:8]),
        tenant=TenantId("tenant-a"),
        goal="quota goal",
        agent_catalog=(AgentSpec(name="scout", role="worker"),),
        provider=ProviderKind("stub"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_envelope(name="scout") -> TaskEnvelope:
    return TaskEnvelope(
        name=name, objective="do the thing", output_schema={}, boundaries="",
        parent_goal="quota goal",
    )


class _SetupOutcomeSubagent:
    """Double whose setup raises a scripted per-attempt exception."""

    def __init__(self, spec: AgentSpec, exc: BaseException) -> None:
        self.name = spec.name
        self.executor_id = "ac_pending" if isinstance(
            exc, NonRetryableProvisioningError) else "ac_partial"
        self.executor_name = spec.name
        self._exc = exc

    async def setup(self) -> None:
        raise self._exc

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        raise AssertionError("run_task must never run when setup fails")

    async def teardown(self) -> None:
        pass

    async def halt(self) -> None:
        pass


class _SetupOutcomeFactory:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.creates = 0

    def create(self, spec, run, agent_id=None, attempt=1):
        self.creates += 1
        return _SetupOutcomeSubagent(spec, self.exc)


class TestNonRetryableBreaksOnAttemptOne:

    def test_non_retryable_setup_error_fails_on_attempt_1(self, caplog):
        async def main():
            factory = _SetupOutcomeFactory(NonRetryableProvisioningError(
                "bot quota exceeded for personal:owner"))
            breaker = UpstreamBreaker(failure_threshold=1, open_seconds=60.0)
            rt = SubagentRuntime(
                factory=factory, max_attempts=3, upstream_breaker=breaker)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return factory, breaker, results

        with caplog.at_level(
            logging.WARNING,
            logger="agentclaw.community.core.agentswarm.runtime.pool.impl",
        ):
            factory, breaker, results = asyncio.run(main())

        (r,) = results
        assert r["status"].value == "FAILED"
        assert r["attempts"] == 1
        assert "bot quota exceeded" in r["error"]
        assert "NonRetryableProvisioningError" in r["error"]
        # The budget was never burned and the breaker was never fed.
        assert factory.creates == 1
        assert breaker.state is BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        warns = [rec for rec in caplog.records
                if "non_retryable" in rec.getMessage()]
        assert warns and "NonRetryableProvisioningError" in warns[0].getMessage()

    def test_retryable_setup_error_still_burns_full_budget(self):
        async def main():
            factory = _SetupOutcomeFactory(RuntimeError("transient boom"))
            rt = SubagentRuntime(factory=factory, max_attempts=3)
            await rt.start()
            try:
                await rt.spawn(_mk_run(), _mk_envelope())
                results = await rt.wait_for_completions()
            finally:
                await rt.stop()
            return factory, results

        factory, results = asyncio.run(main())
        (r,) = results
        assert r["status"].value == "FAILED"
        assert r["attempts"] == 3
        assert r["error"] == "transient boom"
        assert factory.creates == 3