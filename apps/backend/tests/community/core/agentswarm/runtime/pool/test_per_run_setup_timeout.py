"""Per-run setup-timeout override tests.

A run whose ``run_options.setup_timeout_seconds`` is set re-budgets that
run's provisioning phase (spawn + authorization + READY). Absent it falls
to the pod's configured ``orchestration.setup_timeout``. A requested
value wins up to the slice-room ceiling (``slice_max_seconds`` minus
``per_attempt_timeout`` from the deployment config) so a caller can
lengthen slow-env provisioning from the request alone, while one
agent's setup can never outgrow what a single slice can hold.
"""
from __future__ import annotations

import uuid

from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, EpochMillis, ProviderKind, RequestMetadata, Run, RunId,
    RunOptions, RunStatus, TaskEnvelope, TenantId,
)
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime
import time


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(options: RunOptions | None) -> Run:
    return Run(
        run_id=RunId("run-" + uuid.uuid4().hex[:8]),
        tenant=TenantId("tenant-a"),
        goal="setup budget goal",
        agent_catalog=(AgentSpec(name="scout", role="worker"),),
        provider=ProviderKind("stub"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
        run_options=options,
    )


def _mk_runtime(setup_timeout: float = 300.0,
                setup_ceiling: float = 600.0,
                per_task_timeout: float = 300.0) -> SubagentRuntime:
    class _NoopFactory:
        def create(self, spec, run, agent_id=None, attempt=1):
            raise AssertionError("resolution tests never execute slices")

        def destroy_run_agents_bots(self, run, agents):
            return []

    return SubagentRuntime(factory=_NoopFactory(),
                           max_attempts=1,
                           per_task_timeout=per_task_timeout,
                           setup_timeout=setup_timeout,
                           setup_ceiling=setup_ceiling)


class TestResolvedSetupTimeoutContract:

    def test_absent_falls_to_configured_value(self):
        rt = _mk_runtime(setup_timeout=300.0)
        assert rt._resolve_setup_timeout(_mk_run(RunOptions())) == 300.0

    def test_none_run_options_fall_to_configured_value(self):
        rt = _mk_runtime(setup_timeout=240.0)
        assert rt._resolve_setup_timeout(_mk_run(None)) == 240.0

    def test_requested_lower_tightens(self):
        rt = _mk_runtime(setup_timeout=300.0)
        run = _mk_run(RunOptions(setup_timeout_seconds=30.0))
        assert rt._resolve_setup_timeout(run) == 30.0

    def test_requested_higher_than_config_wins_up_to_ceiling(self):
        # The deployment's own setup_timeout (300) is a fallback, not a
        # cap: PRE's slice room (900 - 300) admits 600 from the request.
        rt = _mk_runtime(setup_timeout=300.0, setup_ceiling=600.0)
        run = _mk_run(RunOptions(setup_timeout_seconds=600.0))
        assert rt._resolve_setup_timeout(run) == 600.0

    def test_requested_is_clamped_to_slice_room_ceiling(self):
        rt = _mk_runtime(setup_timeout=300.0, setup_ceiling=600.0)
        run = _mk_run(RunOptions(setup_timeout_seconds=900.0))
        assert rt._resolve_setup_timeout(run) == 600.0

    def test_requested_is_floored_at_one_second(self):
        rt = _mk_runtime(setup_timeout=300.0)
        run = _mk_run(RunOptions(setup_timeout_seconds=0.0))
        assert rt._resolve_setup_timeout(run) == 1.0


class TestRunOptionsSetupFieldSurfaces:

    def test_from_dto_carries_setup_timeout(self):

        class _Dto:
            model_name = None
            slice_max_seconds = None
            per_attempt_timeout_seconds = None
            setup_timeout_seconds = 600.0
            max_turns = None
            max_total_agents = None
            max_width = None
            max_attempts = None

        opts = RunOptions.from_dto(_Dto())
        assert opts.setup_timeout_seconds == 600.0
        assert opts.resolved_setup_timeout(300.0, 600.0) == 600.0

    def test_metadata_roundtrip_carries_setup_timeout(self):
        opts = RunOptions(setup_timeout_seconds=450.0)
        md = RequestMetadata()
        opts.to_metadata(md)
        back = RunOptions.from_metadata(md)
        assert back.setup_timeout_seconds == 450.0


class TestResolvedPerAttemptContract:

    def test_absent_falls_to_configured_value(self):
        rt = _mk_runtime(per_task_timeout=300.0)
        assert rt._resolve_per_task_timeout(_mk_run(RunOptions())) == 300.0

    def test_none_run_options_fall_to_configured_value(self):
        rt = _mk_runtime(per_task_timeout=300.0)
        assert rt._resolve_per_task_timeout(_mk_run(None)) == 300.0

    def test_requested_lower_tightens(self):
        rt = _mk_runtime(per_task_timeout=300.0)
        run = _mk_run(RunOptions(per_attempt_timeout_seconds=30.0))
        assert rt._resolve_per_task_timeout(run) == 30.0

    def test_requested_higher_is_clamped_to_config(self):
        rt = _mk_runtime(per_task_timeout=300.0)
        run = _mk_run(RunOptions(per_attempt_timeout_seconds=9999.0))
        assert rt._resolve_per_task_timeout(run) == 300.0


class TestResolvedSliceMaxContract:

    def test_absent_falls_to_configured_value(self):
        opts = RunOptions()
        assert opts.resolved_slice_max(900.0) == 900.0

    def test_requested_lower_tightens(self):
        opts = RunOptions(slice_max_seconds=60.0)
        assert opts.resolved_slice_max(900.0) == 60.0

    def test_requested_higher_is_clamped_to_config(self):
        opts = RunOptions(slice_max_seconds=9999.0)
        assert opts.resolved_slice_max(900.0) == 900.0