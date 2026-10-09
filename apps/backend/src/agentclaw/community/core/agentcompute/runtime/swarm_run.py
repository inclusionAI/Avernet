from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.domain.models import (
    Agent, AgentId, AgentKind, AgentStatus, IdempotencyKey, Run, RunId,
    RunStatus, TenantId, TerminationReason, WaitMode,
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
from agentclaw.community.core.agentcompute.runtime.swarm_store import SliceStore

logger = logging.getLogger(__name__)


class _SliceLost(Exception):
    """Cooperative abort: the slice's claim was lost mid-flight (D28)."""


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
        self._active_run_id: RunId | None = None
        self._active_tenant: TenantId | None = None
        self.store = SliceStore(
            config, agent_repo, message_repo, shard_repo, round_repo,
            lost_probe=lambda: self._slice_lost.is_set(),
        )

    def set_slice_lost(self, lost: bool, run_id: Any = None) -> None:
        if run_id is not None and run_id != self._active_run_id:
            # D28/H4: a lost claim for a run that is not inflight here must
            # not abort the slice currently being executed by this worker.
            logger.info(
                "[agentcompute][swarm] slice-lost for run=%s ignored (not inflight)", run_id,
            )
            return
        if lost:
            self._slice_lost.set()
            self._runtime.set_slice_lost(True)
        else:
            self._slice_lost.clear()
            self._runtime.set_slice_lost(False)

    def force_stuck(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                    error: str, worker_id: str | None = None) -> bool:
        """D40 reclaim circuit breaker: transition a poison run to terminal STUCK."""
        if worker_id is not None:
            self._worker_id = worker_id
        run = self._run_repo.get(run_id, tenant)
        if run is None or run.status is not RunStatus.RUNNING:
            self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
            return False
        ok = self._run_repo.update_status(
            run_id, tenant, RunStatus.STUCK,
            termination_reason=TerminationReason.STUCK,
            error=error, claim_epoch=claim_epoch,
        )
        if ok:
            self.store.apply_run_retention(run_id, tenant)
        self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
        return ok

    async def handle_slice(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                           cancel_requested: bool = False, *,
                           worker_id: str | None = None) -> str:
        """Run one bounded slice. Returns: "complete" | "reschedule" | "retry" | "fail" | "stuck".

        Cooperative cancel: when SliceLost is set mid-slice, the runtime tears
        down in-flight subagents and we return without persisting further
        state mutations for this slice (the reclaimer owns the outcome).

        The slice is a multi-turn loop bounded by orchestration.slice_max_seconds;
        completions are drained and durably persisted before each orchestrator
        decision, so any pod can resume from shards alone (D34).
        """
        self._claim_epoch = claim_epoch
        if worker_id is not None:
            self._worker_id = worker_id
        self._active_run_id = run_id
        self._active_tenant = tenant
        self.set_slice_lost(False, run_id)

        if cancel_requested:
            run = self._run_repo.get(run_id, tenant)
            if run is not None and run.status is RunStatus.RUNNING:
                if self._run_repo.update_status(
                    run_id, tenant, RunStatus.CANCELLED,
                    termination_reason=TerminationReason.CANCELLED,
                    claim_epoch=claim_epoch,
                ):
                    self.store.apply_run_retention(run_id, tenant)
            self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
            return "complete"

        run = self._run_repo.get(run_id, tenant)
        if run is None:
            return "fail"
        if run.status is not RunStatus.RUNNING:
            self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
            return "complete"

        state, done_pairs, interrupted = self.store.rehydrate(run)
        self._run_repo.update_status(run_id, tenant, RunStatus.RUNNING, claim_epoch=claim_epoch)
        deadline = self._deadline()

        try:
            await self._runtime.start()
            for agent in interrupted:
                # D16/D24: re-instantiate interrupted agents under their existing
                # identity and idempotency key — never under a fresh AgentId.
                await self._runtime.spawn(
                    run, agent.task_spec,
                    agent_id=AgentId(agent.agent_id),
                    idempotency_key=agent.idempotency_key,
                )
            while True:
                self._check_lost()
                self._drain_completions(
                    await self._runtime.wait_for_completions(timeout=0), state,
                )
                self._check_lost()

                outcome = await self._orchestrator.run_slice(
                    run, state,
                    shard_getter=lambda rid: self.store.get_shards(rid, tenant),
                    on_llm_exchange=self._on_llm_exchange,
                )
                self._check_lost()
                for ge in outcome.get("guard_events", []):
                    self._guard_repo.create(ge)

                term = outcome.get("terminate") or self._termination.evaluate(state)
                if outcome.get("fatal") is not None:
                    return self._finalize_failure(run_id, tenant, term, outcome, claim_epoch)
                if term is not None:
                    return self._finalize(run_id, tenant, term, outcome, claim_epoch)

                if outcome.get("wait_refs") is not None:
                    await self._await_refs(
                        run_id, tenant, outcome["wait_refs"],
                        outcome.get("wait_mode") or WaitMode.ALL, deadline, state,
                    )
                elif outcome.get("spawn"):
                    await self._dispatch_spawn(run, state, outcome["spawn"], done_pairs)
                elif outcome.get("persona") is not None:
                    pass  # handoff: persona applied to state + next prompt already

                if time.monotonic() >= deadline:
                    break
                if outcome.get("wait_refs") is None:
                    # D28/H1: bound the decision cadence so a non-wait turn
                    # cannot hot-spin against the LLM.
                    await asyncio.sleep(
                        min(self._config.orchestration.orchestrator_turn_interval,
                            max(0.0, deadline - time.monotonic()))
                    )
                self._check_lost()
        except _SliceLost:
            self._release_claim(run_id, tenant, claim_epoch, "slice lost")
            return "retry"
        except asyncio.CancelledError:
            # D23: cancellation propagates after cleanup — never swallowed.
            self._release_claim(run_id, tenant, claim_epoch, "slice cancelled")
            raise
        finally:
            self._active_run_id = None
            self._active_tenant = None
            await self._runtime.stop()

        self._job_repo.reschedule(
            run_id, tenant, self._worker_id,
            self._config.orchestration.slice_interval, None, claim_epoch,
        )
        return "reschedule"

    def _deadline(self) -> float:
        return time.monotonic() + self._config.orchestration.slice_max_seconds

    def _release_claim(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                       note: str) -> None:
        self._job_repo.reschedule(
            run_id, tenant, self._worker_id,
            self._config.orchestration.slice_interval, note, claim_epoch,
        )

    def _finalize(self, run_id: RunId, tenant: TenantId, term: TerminationReason,
                  outcome: dict[str, Any], claim_epoch: int) -> str:
        ok = self._run_repo.update_status(
            run_id, tenant, self._resolve_final_status(term),
            termination_reason=term,
            final_answer=outcome.get("finish_answer")
            if term == TerminationReason.FINISH else None,
            claim_epoch=claim_epoch,
        )
        if not ok:
            # Fenced off: the slice was reclaimed; the new owner decides.
            raise _SliceLost()
        self.store.apply_run_retention(run_id, tenant)
        self._job_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
        return "complete"

    def _finalize_failure(self, run_id: RunId, tenant: TenantId,
                          term: TerminationReason, outcome: dict[str, Any],
                          claim_epoch: int) -> str:
        ok = self._run_repo.update_status(
            run_id, tenant, self._resolve_final_status(term or TerminationReason.ERROR),
            termination_reason=term or TerminationReason.ERROR,
            error=outcome.get("fatal"),
            claim_epoch=claim_epoch,
        )
        if not ok:
            raise _SliceLost()
        self.store.apply_run_retention(run_id, tenant)
        self._job_repo.fail(
            run_id, tenant, outcome.get("fatal") or "run failed",
            self._worker_id, claim_epoch,
        )
        return "fail"

    def _drain_completions(self, completions: list[dict[str, Any]],
                           state: RunState | None) -> None:
        for completion in completions:
            self.store.persist_completion(completion, self._claim_epoch, state)

    def _on_llm_exchange(self, system_prompt: str, user_prompt: str,
                         response_text: str, token_usage: Any,
                         decision_dict: dict[str, Any]) -> None:
        run_id, tenant = self._active_run_id, self._active_tenant
        if run_id is None or tenant is None or self._slice_lost.is_set():
            return
        self.store.record_llm_exchange(run_id, tenant, user_prompt,
                                       decision_dict, token_usage)

    def _check_lost(self) -> None:
        if self._slice_lost.is_set():
            raise _SliceLost()

    async def _dispatch_spawn(self, run: Run, state: RunState,
                              envelopes: list[Any], done_pairs: set[tuple[str, str]]) -> None:
        cohort: list[AgentId] = []
        for envelope in envelopes:
            if (envelope.name, envelope.objective) in done_pairs:
                # D24: this objective already delivered a persisted shard.
                logger.info(
                    "[agentcompute][swarm] skipping completed spawn name=%s", envelope.name,
                )
                continue
            agent_id = await self._runtime.spawn(run, envelope)
            self.store.create_agent(Agent(
                agent_id=agent_id,
                run_id=run.run_id,
                tenant=run.tenant,
                parent_agent_id=None,
                depth=0,
                name=envelope.name,
                role=envelope.objective,
                kind=self._resolve_kind(run, envelope.name),
                task_spec=envelope,
                spawn_round=state.round_index,
                status=AgentStatus.QUEUED,
                attempts=0,
                idempotency_key=IdempotencyKey(f"{run.run_id}:{agent_id}"),
                output={},
            ))
            done_pairs.add((envelope.name, envelope.objective))
            cohort.append(agent_id)
        if cohort:
            self.store.create_round(run, state.round_index, cohort)
            state.round_index += 1

    @staticmethod
    def _resolve_kind(run: Run, name: str) -> AgentKind:
        for spec in run.agent_catalog:
            if spec.name == name:
                return spec.kind if isinstance(spec.kind, AgentKind) else AgentKind.LLM
        return AgentKind.LLM

    async def _await_refs(self, run_id: RunId, tenant: TenantId, refs: tuple[str, ...],
                          mode: WaitMode, deadline: float,
                          state: RunState | None = None) -> None:
        while True:
            unsatisfied = self.store.unsatisfied_refs(run_id, tenant, refs)
            if not unsatisfied:
                return
            if mode == WaitMode.ANY and len(unsatisfied) < len(refs):
                return
            self._check_lost()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Budget exhausted mid-wait: persisted shards are the durable
                # continuation point for the next slice (D34).
                return
            self._drain_completions(
                await self._runtime.wait_for_completions(timeout=min(remaining, 0.25)),
                state,
            )

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