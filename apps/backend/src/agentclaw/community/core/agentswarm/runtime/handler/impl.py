from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from agentclaw.community.core.agentswarm.config.settings import AgentSwarmConfig
from agentclaw.community.core.agentswarm.domain.models import (
    Agent, AgentId, AgentKind, AgentStatus, IdempotencyKey, Run, RunId,
    RunStatus, TenantId, TerminationReason, WaitMode,
)
from agentclaw.community.core.agentswarm.orchestration.orchestrator import Orchestrator
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.state import RunState
from agentclaw.community.core.agentswarm.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime
from agentclaw.community.core.agentswarm.runtime.offload import run_blocking
from agentclaw.community.core.agentswarm.runtime.slice_lost import SliceLost
from agentclaw.community.core.agentswarm.runtime.store import SliceStore
from agentclaw.community.log import get_logger

logger = get_logger("agentswarm")


# Back-compat alias: the lost-claim signal moved to runtime/slice_lost.py
# when the pool (which imports the store) started needing the type; every
# pre-existing import of _SliceLost from this module keeps working.
_SliceLost = SliceLost


class SwarmRunHandler:

    def __init__(
        self,
        config: AgentSwarmConfig,
        orchestrator: Orchestrator,
        runtime: SubagentRuntime,
        run_repo: RunRepository,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        result_repo: AgentResultRepository,
        round_repo: RoundRepository,
        guard_repo: GuardEventRepository,
        dispatch_repo: RunDispatchRepository,
        termination: TerminationEvaluator,
        guards: GuardService,
    ) -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._runtime = runtime
        self._run_repo = run_repo
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._result_repo = result_repo
        self._round_repo = round_repo
        self._guard_repo = guard_repo
        self._dispatch_repo = dispatch_repo
        self._termination = termination
        self._guards = guards
        self._slice_lost = asyncio.Event()
        self._claim_epoch = 0
        self._worker_id = uuid.uuid4().hex[:8]
        self._active_run_id: RunId | None = None
        self._active_tenant: TenantId | None = None
        self._finalized = False
        self.store = SliceStore(
            config, agent_repo, message_repo, result_repo, round_repo,
            lost_probe=lambda: self._slice_lost.is_set(),
            guard_repo=guard_repo,
        )

    def set_slice_lost(self, lost: bool, run_id: Any = None) -> None:
        if run_id is not None and run_id != self._active_run_id:
            # D28/H4: a lost claim for a run that is not inflight here must
            # not abort the slice currently being executed by this worker.
            logger.info(
                "[agentswarm][swarm] slice-lost for run=%s ignored (not inflight)", run_id,
            )
            return
        if lost:
            self._slice_lost.set()
            self._runtime.set_slice_lost(True)
        else:
            self._slice_lost.clear()
            self._runtime.set_slice_lost(False)

    def _destroy_run_bot_personas(self, run_id: RunId, tenant: TenantId) -> None:
        # Lifecycle contract: any failed or cancelled run destroys every bot
        # it provisioned. Best-effort after a successful terminal write — a
        # cleanup error never blocks the transition.
        try:
            run = self._run_repo.get(run_id, tenant)
            if run is None:
                return
            agents = self._agent_repo.list_by_run(run_id, tenant)
            self._runtime.dispose_run_agents_bots(run, agents)
        except Exception:
            logger.exception(
                "[agentswarm][swarm] post-terminal bot cleanup failed for run=%s",
                run_id,
            )

    def ensure_run_cleanup(self, run_id: RunId, tenant: TenantId) -> bool:
        """Idempotent teardown guarantee for a TERMINAL run.

        Safe to call from any path, any number of times, any pod: it only
        acts once the run row is terminal, and ``destroy_run_agents_bots``
        converges per ledger row (delete-if-live, then mark DESTROYED), so
        concurrent callers cannot double-delete or resurrect a settled row.
        Returns True when it ran the sweep. A still-RUNNING run returns False
        — its personas are live and the slice owns them.
        """
        run = self._run_repo.get(run_id, tenant)
        if run is None or run.status is RunStatus.RUNNING:
            return False
        self._destroy_run_bot_personas(run_id, tenant)
        return True

    def force_stuck(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                    error: str, worker_id: str | None = None) -> bool:
        """D40 reclaim circuit breaker: transition a poison run to terminal STUCK.

        Synchronous by contract: the worker invokes it through
        asyncio.to_thread, so it already runs off the event loop and its
        repo calls stay direct.
        """
        if worker_id is not None:
            self._worker_id = worker_id
        run = self._run_repo.get(run_id, tenant)
        if run is None or run.status is not RunStatus.RUNNING:
            self._dispatch_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
            return False
        ok = self._run_repo.update_status(
            run_id, tenant, RunStatus.STUCK,
            termination_reason=TerminationReason.STUCK,
            error=error, claim_epoch=claim_epoch,
        )
        if ok:
            self._destroy_run_bot_personas(run_id, tenant)
        self.store.apply_run_retention_blocking(run_id, tenant)
        self._dispatch_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
        return ok

    async def _finalize_cancelled(self, run_id: RunId, tenant: TenantId,
                                  claim_epoch: int) -> str:
        """Terminal CANCELLED through the fenced write, persona teardown,
        retention, and dispatch completion. Shared by the claim-time cancel
        entry and the mid-slice cancel re-probe: a cancel landing after
        the slice claimed the dispatch must terminalize promptly instead of
        waiting out the slice budget spawning further cohorts."""
        if self._finalized:
            await run_blocking(
                self._dispatch_repo.complete, run_id, tenant, self._worker_id,
                claim_epoch,
            )
            return "complete"
        # Stop the pool first: in-flight attempts must stop persisting
        # and retried provisioning must not mint new personas once the
        # cancellation is being applied (the drain's cooperative-unwind
        # idiom — lost_probe gates every journal/persist write).
        self.set_slice_lost(True, run_id)
        run = await run_blocking(self._run_repo.get, run_id, tenant)
        if run is not None and run.status is RunStatus.RUNNING:
            if await run_blocking(
                self._run_repo.update_status,
                run_id, tenant, RunStatus.CANCELLED,
                termination_reason=TerminationReason.CANCELLED,
                claim_epoch=claim_epoch,
            ):
                await run_blocking(
                    self._destroy_run_bot_personas, run_id, tenant,
                )
                await self.store.apply_run_retention(run_id, tenant)
        await run_blocking(
            self._dispatch_repo.complete, run_id, tenant, self._worker_id,
            claim_epoch,
        )
        return "complete"

    async def _finalize_stuck_dispatch(self, run_id: RunId, tenant: TenantId,
                                         claim_epoch: int,
                                         ref: dict[str, Any]) -> str:
        """D40 terminal for a run looping on a resultless slice_lost ref."""
        detail = (
            f"slice_lost reclaim circuit breaker: resultless agent "
            f"agent_id={ref.get('agent_id')} name={ref.get('name')} "
            f"attempts={ref.get('attempts')}"
        )
        ok = await run_blocking(
            self._run_repo.update_status,
            run_id, tenant, RunStatus.STUCK,
            termination_reason=TerminationReason.STUCK,
            error=detail, claim_epoch=claim_epoch,
        )
        if not ok:
            raise _SliceLost()
        await run_blocking(self._destroy_run_bot_personas, run_id, tenant)
        await self.store.apply_run_retention(run_id, tenant)
        await run_blocking(
            self._dispatch_repo.complete, run_id, tenant, self._worker_id, claim_epoch,
        )
        return "stuck"

    def force_fail(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                   error: str, worker_id: str | None = None) -> bool:
        """13.6 degradation: an unrecoverable slice error terminally fails the
        run so it can never be left RUNNING with a dead dispatch (stalled).

        Synchronous by contract: the worker invokes it through
        asyncio.to_thread, so it already runs off the event loop.
        """
        if worker_id is not None:
            self._worker_id = worker_id
        ok = self._run_repo.update_status(
            run_id, tenant, RunStatus.FAILED,
            termination_reason=TerminationReason.ERROR,
            error=error, claim_epoch=claim_epoch,
        )
        if not ok:
            # D28 fence rejected this claim's write — a newer worker owns the
            # run and its outcome; this slice must not touch the dispatch either.
            return False
        self._destroy_run_bot_personas(run_id, tenant)
        self.store.apply_run_retention_blocking(run_id, tenant)
        self._dispatch_repo.complete(run_id, tenant, self._worker_id, claim_epoch)
        return True

    async def handle_slice(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                           cancel_requested: bool = False, *,
                           worker_id: str | None = None) -> str:
        """Run one bounded slice. Returns: "complete" | "reschedule" | "retry" | "fail" | "stuck".

        Cooperative cancel: when SliceLost is set mid-slice, the runtime tears
        down in-flight attempts and we return without persisting further
        state mutations for this slice (the reclaimer owns the outcome).

        The slice is a multi-turn loop bounded by orchestration.slice_max_seconds;
        completions are drained and durably persisted before each orchestrator
        decision, so any pod can resume from results alone (D34).

        Every repo call on this loop path is offloaded through
        runtime.offload.run_blocking so worker heartbeats and drain signals
        never stall behind a write (D19: the thread hop wraps the whole,
        session-scoped repo call; a session never spans an await).
        """
        self._claim_epoch = claim_epoch
        if worker_id is not None:
            self._worker_id = worker_id
        self._active_run_id = run_id
        self._active_tenant = tenant
        self._finalized = False
        self.set_slice_lost(False, run_id)
        # The slice's fencing token for spawn-time journaling (D-K/D-L):
        # every WorkItem the pool enqueues in this slice snapshots it.
        self._runtime.set_claim_epoch(claim_epoch)

        if cancel_requested:
            return await self._finalize_cancelled(run_id, tenant, claim_epoch)

        run = await run_blocking(self._run_repo.get, run_id, tenant)
        if run is None:
            return "fail"
        if run.status is not RunStatus.RUNNING:
            await run_blocking(
                self._dispatch_repo.complete, run_id, tenant, self._worker_id,
                claim_epoch,
            )
            return "complete"

        state, done_pairs, interrupted = await self.store.rehydrate(
            run, claim_epoch,
        )
        # D34 crash/lost-claim survival: if a crash parked this
        # slice mid-wait on a ref whose agent is now FAILED, the wait is
        # unsatisfiable — fail fast through the same fenced path instead
        # of respawning the doomed wait.
        if state.pending_wait_refs:
            failed_ref = await self.store.first_failed_wait_ref(
                run_id, tenant, state.pending_wait_refs,
            )
            if failed_ref is not None:
                return await self._abort_failed_blocking_ref(
                    run_id, tenant, failed_ref, claim_epoch,
                )
        await run_blocking(
            self._run_repo.update_status,
            run_id, tenant, RunStatus.RUNNING, claim_epoch=claim_epoch,
        )
        deadline = self._deadline(run)

        try:
            await self._runtime.start()
            reschedule_slice_lost = False
            for agent in interrupted:
                # D16/D24: re-instantiate interrupted agents under their existing
                # identity and idempotency key — never under a fresh AgentId.
                await self._runtime.spawn(
                    run, agent.task_spec,
                    agent_id=AgentId(agent.agent_id),
                    idempotency_key=agent.idempotency_key,
                )
            while True:
                if await run_blocking(
                    self._dispatch_repo.is_cancel_requested, run_id, tenant,
                ):
                    # A cancel that landed after this slice claimed the dispatch
                    # must not wait out the slice budget (up to
                    # slice_max_seconds of further spawning) — honor it
                    # between turns.
                    return await self._finalize_cancelled(
                        run_id, tenant, claim_epoch,
                    )
                self._check_lost()
                await self._drain_completions(
                    await self._runtime.wait_for_completions(timeout=0), state,
                )
                self._check_lost()

                outcome = await self._orchestrator.decide_turn(
                    run, state,
                    result_getter=lambda rid: self.store.get_results(rid, tenant),
                    on_llm_exchange=self._on_llm_exchange,
                )
                self._check_lost()
                for ge in outcome.get("guard_events", []):
                    await run_blocking(self._guard_repo.create, ge)

                term = outcome.get("terminate") or self._termination.evaluate(state)
                if outcome.get("fatal") is not None:
                    return await self._finalize_failure(
                        run_id, tenant, term, outcome, claim_epoch,
                    )
                if term is not None:
                    return await self._finalize(run_id, tenant, term, outcome, claim_epoch)

                if outcome.get("wait_refs") is not None:
                    failed_ref = await self._await_refs(
                        run_id, tenant, outcome["wait_refs"],
                        outcome.get("wait_mode") or WaitMode.ALL, deadline, state,
                    )
                    if (isinstance(failed_ref, dict)
                            and "_reschedule_slice_lost" in failed_ref):
                        # Count the no-progress reschedule and trip the D40
                        # breaker ourselves: a PENDING reschedule is claimed
                        # via claim_batch (reclaimed=False), so the worker's
                        # reclaim_batch breaker check never sees it.
                        reclaims = await run_blocking(
                            self._dispatch_repo.bump_reclaim,
                            run_id, tenant, self._worker_id, claim_epoch,
                        )
                        if (reclaims is not None
                                and reclaims > self._config.orchestration.reclaim_max):
                            return await self._finalize_stuck_dispatch(
                                run_id, tenant, claim_epoch,
                                failed_ref["_reschedule_slice_lost"],
                            )
                        reschedule_slice_lost = True
                        break
                    if failed_ref is not None:
                        # A FAILED agent blocks an ACTIVE wait ref its
                        # result can never satisfy — abort the run
                        # instead of wedging the slice forever.
                        return await self._abort_failed_blocking_ref(
                            run_id, tenant, failed_ref, claim_epoch,
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
            await self._release_claim(run_id, tenant, claim_epoch, "slice lost")
            return "retry"
        except asyncio.CancelledError:
            # D23: cancellation propagates after cleanup — never swallowed.
            await self._release_claim(run_id, tenant, claim_epoch, "slice cancelled")
            raise
        finally:
            self._active_run_id = None
            self._active_tenant = None
            await self._runtime.stop()

        if reschedule_slice_lost:
            await self._release_claim(
                run_id, tenant, claim_epoch, "slice_lost resultless ref",
            )
            return "reschedule"
        await run_blocking(
            self._dispatch_repo.reschedule,
            run_id, tenant, self._worker_id,
            self._config.orchestration.slice_interval, None, claim_epoch,
        )
        return "reschedule"

    def _deadline(self, run) -> float:
        opts = getattr(run, "run_options", None)
        budget = (opts.resolved_slice_max(
                      self._config.orchestration.slice_max_seconds)
                  if opts is not None
                  else float(self._config.orchestration.slice_max_seconds))
        return time.monotonic() + budget

    async def _release_claim(self, run_id: RunId, tenant: TenantId, claim_epoch: int,
                             note: str) -> None:
        await run_blocking(
            self._dispatch_repo.reschedule,
            run_id, tenant, self._worker_id,
            self._config.orchestration.slice_interval, note, claim_epoch,
        )

    async def _finalize(self, run_id: RunId, tenant: TenantId, term: TerminationReason,
                        outcome: dict[str, Any], claim_epoch: int) -> str:
        ok = await run_blocking(
            self._run_repo.update_status,
            run_id, tenant,
            self._resolve_final_status(term),
            termination_reason=term,
            final_answer=outcome.get("finish_answer")
            if term == TerminationReason.FINISH else None,
            claim_epoch=claim_epoch,
        )
        if not ok:
            # Fenced off: the slice was reclaimed; the new owner decides.
            raise _SliceLost()
        await run_blocking(self._destroy_run_bot_personas, run_id, tenant)
        await self.store.apply_run_retention(run_id, tenant)
        await run_blocking(
            self._dispatch_repo.complete, run_id, tenant, self._worker_id,
            claim_epoch,
        )
        return "complete"

    async def _finalize_failure(self, run_id: RunId, tenant: TenantId,
                                term: TerminationReason, outcome: dict[str, Any],
                                claim_epoch: int) -> str:
        ok = await run_blocking(
            self._run_repo.update_status,
            run_id, tenant,
            self._resolve_final_status(term or TerminationReason.ERROR),
            termination_reason=term or TerminationReason.ERROR,
            error=outcome.get("fatal"),
            claim_epoch=claim_epoch,
        )
        if not ok:
            raise _SliceLost()
        await run_blocking(self._destroy_run_bot_personas, run_id, tenant)
        await self.store.apply_run_retention(run_id, tenant)
        await run_blocking(
            self._dispatch_repo.fail,
            run_id, tenant, outcome.get("fatal") or "run failed",
            self._worker_id, claim_epoch,
        )
        return "fail"

    async def _drain_completions(self, completions: list[dict[str, Any]],
                                  state: RunState | None) -> None:
        for completion in completions:
            await self.store.persist_completion(
                completion, self._claim_epoch, state,
            )

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
                # D24: this objective already delivered a persisted result.
                logger.info(
                    "[agentswarm][swarm] skipping completed spawn name=%s", envelope.name,
                )
                continue
            kind = self._resolve_kind(run, envelope.name)
            agent_id = await self._runtime.spawn(run, envelope)
            await self.store.create_agent(Agent(
                agent_id=agent_id,
                run_id=run.run_id,
                tenant=run.tenant,
                parent_agent_id=None,
                depth=0,
                name=envelope.name,
                role=envelope.objective,
                kind=kind,
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
            await self.store.create_round(run, state.round_index, cohort)
            state.round_index += 1

    @staticmethod
    def _resolve_kind(run: Run, name: str) -> AgentKind:
        for spec in run.agent_catalog:
            if spec.name == name:
                return spec.kind if isinstance(spec.kind, AgentKind) else AgentKind.LLM
        return AgentKind.LLM

    async def _await_refs(self, run_id: RunId, tenant: TenantId, refs: tuple[str, ...],
                          mode: WaitMode, deadline: float,
                          state: RunState | None = None) -> dict[str, Any] | None:
        """Block until the refs are satisfiable; never wedge on dead agents.

        Returns the failed-blocking agent dict when a FAILED agent
        blocks an ACTIVE (unsatisfied) wait ref — evaluated AFTER a full
        batch drain, so a batch that satisfies the goal (or the ANY
        mode's needed ref) wins over a failure landing in the same
        batch. Returns ``{"_reschedule_slice_lost": {...}}`` when a
        resultless FAILED slice_lost agent parks the wait (the in-slice
        respawn re-fences at this claim epoch, so the ref can never be
        satisfied here) — the sentinel tells ``handle_slice`` to
        reschedule immediately. Budget exhaustion still returns None
        (D34: persisted results are the durable continuation point)."""
        while True:
            unsatisfied = await self.store.unsatisfied_refs(run_id, tenant, refs)
            if not unsatisfied:
                return None
            # An unsatisfied ref with NO agent row is a deferred spawn
            # (width/depth cap rejected it this slice), not a dead branch:
            # yield to the next turn so the orchestrator re-emits the spawn
            # once capacity frees. Never fail, never slice_lost.
            unspawned = await self.store.unspawned_wait_refs(
                run_id, tenant, unsatisfied)
            if unspawned == tuple(unsatisfied):
                return None
            if mode == WaitMode.ANY and len(unsatisfied) < len(refs):
                return None
            failed = await self.store.first_failed_wait_ref(
                run_id, tenant, unsatisfied,
            )
            if failed is not None:
                return failed
            # A resultless FAILED slice_lost agent is revivable by the next
            # claim's rehydrate prologue, but NOT by this slice: its respawn
            # re-fences at our claim epoch. Return the sentinel so
            # handle_slice reschedules NOW instead of blocking to the
            # deadline and looping as an uncounted READY claim.
            slice_lost = await self.store.first_slice_lost_wait_ref(
                run_id, tenant, unsatisfied,
            )
            if slice_lost is not None:
                return {"_reschedule_slice_lost": slice_lost}
            self._check_lost()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if await run_blocking(
                self._dispatch_repo.is_cancel_requested, run_id, tenant,
            ):
                return None
            await self._drain_completions(
                await self._runtime.wait_for_completions(timeout=min(remaining, 0.25)),
                state,
            )

    async def _abort_failed_blocking_ref(self, run_id: RunId, tenant: TenantId,
                                          failed: dict[str, Any],
                                          claim_epoch: int) -> str:
        """Terminate the run because a FAILED agent blocks an awaited ref.

        The run cannot make progress, so it is failed through the fenced
        ``_finalize_failure`` write (``TerminationReason.ERROR``), carrying
        the failed agent's id, attempts, and error in the run's error
        string. ``self._finalized`` makes it fire at most once per slice.
        """
        if self._finalized:
            return "fail"
        self._finalized = True
        detail = (
            f"failed agent blocks awaited refs: "
            f"agent_id={failed.get('agent_id')} name={failed.get('name')} "
            f"attempts={failed.get('attempts')} error={failed.get('error')!r}"
        )
        return await self._finalize_failure(
            run_id, tenant, TerminationReason.ERROR,
            {"fatal": detail}, claim_epoch,
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