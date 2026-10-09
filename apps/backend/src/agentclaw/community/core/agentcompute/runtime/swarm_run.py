from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.domain.models import (
    Run, RunId, RunStatus, TenantId, TerminationReason, EpochMillis,
)
from agentclaw.community.core.agentcompute.orchestration.orchestrator import Orchestrator
from agentclaw.community.core.agentcompute.orchestration.termination import (
    RunState, TerminationEvaluator, GuardService,
)
from agentclaw.community.core.agentcompute.repository.impl import (
    RunRepository, AgentRepository, MessageRepository,
    ResultShardRepository, RoundRepository, GuardEventRepository, JobRepository,
)
from agentclaw.community.core.agentcompute.runtime.subagent_runtime import SubagentRuntime

logger = logging.getLogger(__name__)


class SwarmRunHandler:

    def __init__(
        self,
        config: AgentComputeConfig,
        orchestrator: Orchestrator,
        runtime: SubagentRuntime,
        run_repo: RunRepository,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        shard_repo: ResultShardRepository,
        round_repo: RoundRepository,
        guard_repo: GuardEventRepository,
        job_repo: JobRepository,
        termination: TerminationEvaluator,
        guards: GuardService,
    ) -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._runtime = runtime
        self._run_repo = run_repo
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._shard_repo = shard_repo
        self._round_repo = round_repo
        self._guard_repo = guard_repo
        self._job_repo = job_repo
        self._termination = termination
        self._guards = guards
        self._slice_lost = asyncio.Event()
        self._claim_epoch = 0
        self._worker_id = uuid.uuid4().hex[:8]

    def set_slice_lost(self, lost: bool) -> None:
        if lost:
            self._slice_lost.set()
            self._runtime.set_slice_lost(True)
        else:
            self._slice_lost.clear()
            self._runtime.set_slice_lost(False)

    async def handle_slice(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                          cancel_requested: bool = False) -> str:
        """Run one bounded slice. Returns: "complete" | "reschedule" | "retry" | "fail" | "stuck".

        Cooperative cancel: when SliceLost is set mid-slice, the runtime tears
        down in-flight subagents and we return without persisting (the reclaimer
        owns the outcome).
        """
        self._claim_epoch = claim_epoch

        if cancel_requested:
            self._run_repo.update_status(
                run_id, tenant, RunStatus.CANCELLED,
                termination_reason=TerminationReason.CANCELLED, claim_epoch=claim_epoch,
            )
            self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
            return "complete"

        run = self._run_repo.get(run_id, tenant)
        if run is None:
            return "fail"
        if run.status is not RunStatus.RUNNING:
            self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
            return "complete"

        state = self._rehydrate_state(run)
        self._run_repo.update_status(
            run_id, tenant, RunStatus.RUNNING, claim_epoch=claim_epoch,
        )

        start_time = time.monotonic()
        slice_budget = self._config.orchestration.slice_max_seconds

        try:
            await self._runtime.start()
            outcome = await self._orchestrator.run_slice(
                run, state,
                shard_getter=lambda rid: self._get_shards(rid, tenant),
            )
        except asyncio.CancelledError:
            self._runtime.set_slice_lost(True)
            await self._runtime.stop()
            return "retry"
        except TimeoutError:
            await self._runtime.stop()
            self._job_repo.reschedule(
                run_id, tenant, self._worker_id,
                self._config.orchestration.slice_interval, "slice timeout",
                claim_epoch,
            )
            return "retry"
        finally:
            if self._slice_lost.is_set():
                await self._runtime.stop()
                return "retry"

        await self._runtime.stop()

        for ge in outcome.get("guard_events", []):
            self._guard_repo.create(ge)

        for envelope in outcome.get("spawn", []):
            agent_id = await self._runtime.spawn(run, envelope)
            from agentclaw.community.core.agentcompute.domain.models import (
                Agent, AgentKind, AgentStatus, IdempotencyKey, BotId,
            )
            import uuid as _uuid
            agent = Agent(
                agent_id=agent_id,
                run_id=run_id,
                parent_agent_id=None,
                depth=0,
                name=envelope.name,
                role=envelope.objective,
                kind=AgentKind.LLM,
                task_spec=envelope,
                spawn_round=state.round_index,
                status=AgentStatus.QUEUED,
                attempts=0,
                idempotency_key=IdempotencyKey(f"{run_id}:{agent_id}"),
                output={},
            )
            self._agent_repo.create(agent)

        term_reason = outcome.get("terminate")
        if term_reason is not None:
            final_status = self._resolve_final_status(term_reason)
            self._run_repo.update_status(
                run_id, tenant, final_status,
                termination_reason=term_reason,
                final_answer=outcome.get("finish_answer"),
                claim_epoch=claim_epoch,
            )
            self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
            return "complete"

        if outcome.get("fatal"):
            self._run_repo.update_status(
                run_id, tenant, RunStatus.FAILED,
                termination_reason=TerminationReason.ERROR,
                error=outcome["fatal"], claim_epoch=claim_epoch,
            )
            self._job_repo.fail(run_id, tenant, outcome["fatal"], self._worker_id, claim_epoch)
            return "fail"

        state.round_index += 1
        elapsed = time.monotonic() - start_time
        if elapsed >= slice_budget:
            self._job_repo.reschedule(
                run_id, tenant, self._worker_id,
                self._config.orchestration.slice_interval, None, claim_epoch,
            )
            return "reschedule"

        self._job_repo.reschedule(
            run_id, tenant, self._worker_id,
            self._config.orchestration.slice_interval, None, claim_epoch,
        )
        return "reschedule"

    @staticmethod
    def _resolve_final_status(term: TerminationReason) -> RunStatus:
        if term == TerminationReason.FINISH:
            return RunStatus.COMPLETED
        if term == TerminationReason.CANCELLED:
            return RunStatus.CANCELLED
        if term == TerminationReason.WALL_CLOCK:
            return RunStatus.TIMED_OUT
        if term == TerminationReason.STUCK:
            return RunStatus.STUCK
        if term == TerminationReason.GUARD:
            return RunStatus.GUARD_KILLED
        return RunStatus.FAILED

    def _get_shards(self, run_id: RunId, tenant: TenantId) -> list:
        shards = self._shard_repo.list_by_run(run_id, tenant)
        return [
            {"agent": s.source_agent_id, "result": s.payload or s.artifact_key}
            for s in shards
        ]

    def _rehydrate_state(self, run: Run) -> RunState:
        state = RunState(run=run)
        rounds = self._round_repo.list_by_run(run.run_id, run.tenant)
        state.round_index = len(rounds)
        agents = self._agent_repo.list_by_run(run.run_id, run.tenant)
        state.total_agents_spawned = len(agents)
        state.routed_shard_count = len(self._shard_repo.list_by_run(run.run_id, run.tenant))
        return state