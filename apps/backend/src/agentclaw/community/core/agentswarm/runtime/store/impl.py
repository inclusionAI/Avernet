from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from agentclaw.community.core.agentswarm.config.settings import AgentSwarmConfig
from agentclaw.community.core.agentswarm.domain.models import (
    Agent, AgentId, AgentKind, AgentStatus, BotState, DecisionKind, EpochMillis, Message, MessageId, MessageRole, AgentResult, Round, RoundId, Run, RunId, ResultId, TenantId, EventId, GuardEvent, GuardKind, EpochMillis,
)
from agentclaw.community.core.agentswarm.orchestration.state import RunState
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.runtime.offload import run_blocking
from agentclaw.community.core.agentswarm.runtime.slice_lost import SliceLost
from agentclaw.community.log import get_logger

logger = get_logger("agentswarm")

# The pool's error string for a FAILED completion produced by a lost claim
# (a reclaimed slice). Not a dead branch: rehydrate respawns it.
_SLICE_LOST_ERROR = "slice_lost"


def _now_ms() -> int:
    return int(time.time() * 1000)


def agent_output(payload: Any, artifact_key: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if payload is not None:
        return {"value": payload}
    if artifact_key is not None:
        return {"artifact_key": artifact_key}
    return {}


class SliceStore:
    """Durable-slice persistence companion for SwarmRunHandler.

    Owns every repository write driven by slice execution: completion
    materialization (agent status + agent result + assistant message, D24
    exactly-once), the per-attempt executor binding journal (D-K, D28
    claim-fenced), orchestrator decision messages (D18/D30), round rows,
    and terminal retention (D13). All writes are short sync repo calls —
    no await inside any orm_session (D19). Repo calls on the worker
    loop's async paths go through runtime.offload.run_blocking (a thread
    hop around each complete, session-scoped repo call — never inside a
    session). ``get_results`` and ``record_llm_exchange`` stay synchronous:
    the orchestrator invokes them through sync callbacks on the loop
    thread, so they cannot be awaited here.
    """

    def __init__(
        self,
        config: AgentSwarmConfig,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        result_repo: AgentResultRepository,
        round_repo: RoundRepository,
        lost_probe: Callable[[], bool],
        guard_repo: "GuardEventRepository | None" = None,
        attempt_repo: "AgentAttemptRepository | None" = None,
    ) -> None:
        self._config = config
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._result_repo = result_repo
        self._round_repo = round_repo
        self._lost_probe = lost_probe
        self._guard_repo = guard_repo
        self._attempt_repo = attempt_repo
        self._last_seq = 0

    def next_seq(self) -> int:
        seq = _now_ms()
        if seq <= self._last_seq:
            seq = self._last_seq + 1
        self._last_seq = seq
        return seq

    async def create_agent(self, agent: Agent) -> Agent:
        return await run_blocking(self._agent_repo.create, agent)

    async def create_round(self, run: Run, stage_index: int,
                           cohort: list[AgentId]) -> None:
        await run_blocking(self._round_repo.create, Round(
            id=RoundId(uuid.uuid4().hex),
            run_id=run.run_id,
            tenant=run.tenant,
            stage_index=stage_index,
            orchestrator_steps=1,
            slowest_attempt_steps=0,
            cohort_agent_ids=tuple(cohort),
            started_at=EpochMillis(_now_ms()),
        ))

    async def persist_completion(self, completion: dict[str, Any], claim_epoch: int,
                                 state: RunState | None = None) -> None:
        if self._lost_probe():
            return
        run_id = completion["run_id"]
        tenant = completion["tenant"]
        agent_id = AgentId(completion["agent_id"])
        status: AgentStatus = completion["status"]
        payload = completion["result"]
        artifact_key = completion["artifact_key"]

        # D28: update_status is claim-epoch fenced — False means the claim
        # was lost and the reclaimer owns this agent's outcome.
        updated = await run_blocking(
            self._agent_repo.update_status,
            run_id, agent_id, tenant, status.value,
            attempts=completion["attempts"],
            output=agent_output(payload, artifact_key),
            error=completion["error"],
            claim_epoch=claim_epoch,
        )
        if not updated:
            logger.info(
                "[agentswarm][swarm] claim fence rejected completion agent=%s "
                "claim_epoch=%d", agent_id, claim_epoch,
            )
            raise SliceLost()
        if status is AgentStatus.COMPLETED:
            await self._record_injection(run_id, tenant, agent_id, payload)
            await self._create_result(run_id, tenant, agent_id, payload,
                                     artifact_key, state)

        content: dict[str, Any] = {"status": status.value}
        if payload is not None:
            content["result"] = payload
        elif artifact_key is not None:
            content["artifact_key"] = artifact_key
        if completion["error"] is not None:
            content["error"] = completion["error"]
        await run_blocking(
            self._message_repo.create,
            self._new_message(run_id, tenant, agent_id, MessageRole.ASSISTANT,
                              content, completion["token_usage"]),
        )

    async def journal_attempt_intent(self, run: Run, agent_id: AgentId,
                                      claim_epoch: int, attempt: int,
                                      executor_kind: str, executor_id: str,
                                      executor_name: str) -> None:
        """Write-ahead the attempt's binding INTENT, before setup().

        Fencing mirrors ``persist_completion`` exactly: the intent insert
        rides the D28 claim fence (the dispatch row's CURRENT claim_epoch) and
        a fenced-out stale slice unwinds as ``SliceLost`` instead of
        journaling for a dead claim — so a lost claim never provisions a
        persona either. ``record_agent_binding`` (post-setup) is the
        settlement leg of this same row.
        """
        if self._lost_probe():
            return
        if self._attempt_repo is None:
            return
        journaled = await run_blocking(
            self._attempt_repo.record_agent_attempt_intent,
            run.run_id, run.tenant, agent_id, claim_epoch, attempt,
            executor_kind, executor_id, executor_name,
        )
        if not journaled:
            logger.info(
                "[agentswarm][swarm] claim fence rejected binding intent "
                "agent=%s attempt=%d claim_epoch=%d", agent_id, attempt,
                claim_epoch,
            )
            raise SliceLost()

    async def journal_attempt_result(self, run: Run, agent_id: AgentId,
                                      claim_epoch: int, attempt: int,
                                      result_status: str,
                                      result_message: str | None) -> None:
        """Best-effort per-attempt outcome write on the ledger (diagnostic).

        Unlike the intent/settle legs this never raises and never unwinds
        as ``SliceLost``: it records a completed attempt's outcome
        (COMPLETED/FAILED/SLICE_LOST/TIMEOUT), and a lost or fenced write
        for a diagnostic field must not fail the completion that produced
        it."""
        if self._lost_probe() or self._attempt_repo is None:
            return
        try:
            updated = await run_blocking(
                self._attempt_repo.record_attempt_result,
                run.run_id, run.tenant, agent_id, claim_epoch, attempt,
                result_status, result_message,
            )
        except Exception:
            logger.warning(
                "[agentswarm][swarm] attempt result journal failed "
                "agent=%s attempt=%d status=%s", agent_id, attempt,
                result_status,
            )
            return
        if not updated:
            logger.info(
                "[agentswarm][swarm] attempt result journal matched no row "
                "agent=%s attempt=%d claim_epoch=%d status=%s", agent_id,
                attempt, claim_epoch, result_status,
            )

    async def record_agent_bot_state(self, run: Run, agent_id: AgentId,
                                     claim_epoch: int, bot_id: str,
                                     bot_state: str) -> None:
        """Fenced best-effort write of a persona lifecycle state.

        The park/terminal legs of a bot persona's setup: unlike
        ``record_agent_binding`` this settles no ledger row — the attempt
        is still in flight — it only mirrors the gate's observable state
        onto the legacy ``ac_agent_swarm_agent.bot_id/bot_state`` columns
        so the run surface (detail, SSE gate deltas, descriptor join)
        reads the park and the authorization's terminal verdicts from the
        same durable row it already composes. Fencing and failure
        semantics follow the binding leg's legacy-column write: the
        claim-epoch predicate rejects a stale slice's write, a fenced or
        missing update is logged and never unwinds the attempt, and a
        store/repository error must not fail the park itself
        (best-effort, like ``journal_attempt_result``).
        """
        if self._lost_probe() or self._agent_repo is None:
            return
        try:
            updated = await run_blocking(
                self._agent_repo.update_status,
                run.run_id, agent_id, run.tenant, AgentStatus.QUEUED.value,
                bot_id=bot_id, bot_state=bot_state, claim_epoch=claim_epoch,
            )
        except Exception:
            logger.warning(
                "[agentswarm][swarm] bot-state journal failed agent=%s "
                "bot=%s state=%s", agent_id, bot_id, bot_state,
            )
            return
        if not updated:
            logger.info(
                "[agentswarm][swarm] bot-state journal matched no row "
                "agent=%s bot=%s state=%s (row absent or fenced)",
                agent_id, bot_id, bot_state,
            )

    async def record_agent_binding(self, run: Run, agent_id: AgentId,
                                    claim_epoch: int, attempt: int,
                                    executor_kind: str, executor_id: str,
                                    executor_name: str) -> None:
        """Settle the attempt's ledger row to BOUND after setup (D-K/D-L).

        Write ordering is D-L: the call happens after ``subagent.setup()``
        returned, so the executor identity is real. Fencing mirrors
        ``persist_completion`` exactly — the settlement rides the D28
        claim fence (the dispatch row's CURRENT claim_epoch), and a fenced-out
        stale slice unwinds as ``SliceLost`` instead of corrupting the
        journal. After the ledger row lands, the legacy
        ``ac_agent_swarm_agent.bot_id/bot_state`` columns get the LATEST
        persona (bot kind only; llm leaves them NULL as today) — that
        same-transaction-style update is best-effort by design: the ledger
        remains the source of truth for destroy, and the agent row may
        legitimately not exist yet when the pool worker races the
        handler's spawn-path ``create_agent``.
        """
        if self._lost_probe():
            return
        if self._attempt_repo is None:
            # Same no-op contract as a missing guard_repo: compositions
            # without the ledger (embedded/test uses) simply do not
            # journal; the DI root always binds one.
            return
        journaled = await run_blocking(
            self._attempt_repo.record_agent_binding,
            run.run_id, run.tenant, agent_id, claim_epoch, attempt,
            executor_kind, executor_id, executor_name,
        )
        if not journaled:
            logger.info(
                "[agentswarm][swarm] claim fence rejected binding journal "
                "agent=%s attempt=%d claim_epoch=%d", agent_id, attempt,
                claim_epoch,
            )
            raise SliceLost()
        if executor_kind != AgentKind.BOT.value:
            return
        updated = await run_blocking(
            self._agent_repo.update_status,
            run.run_id, agent_id, run.tenant, AgentStatus.QUEUED.value,
            bot_id=executor_id, bot_state=BotState.READY.value,
            claim_epoch=claim_epoch,
        )
        if not updated:
            # Not an error: the spawn is mid-create (the handler's
            # create_agent races this journal on the same loop) or the
            # claim epoch moved on. The ledger row is the authoritative
            # destroy record; the legacy column is a LATEST convenience.
            logger.info(
                "[agentswarm][swarm] legacy persona columns not updated "
                "agent=%s executor=%s (row absent or fenced)",
                agent_id, executor_id,
            )

    async def _record_injection(self, run_id: RunId, tenant: TenantId,
                                agent_id: AgentId, payload: Any) -> None:
        from agentclaw.community.core.agentswarm.runtime.security import (
            detect_prompt_injection,
        )
        if self._guard_repo is None or payload is None:
            return
        text = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, default=str,
        )
        markers = detect_prompt_injection(text)
        for marker in markers:
            await run_blocking(
                self._guard_repo.create, GuardEvent(
                    id=EventId(uuid.uuid4().hex),
                    run_id=run_id,
                    tenant=tenant,
                    agent_id=agent_id,
                    kind=GuardKind.PROMPT_INJECTION,
                    details={"marker": marker},
                    created_at=EpochMillis(_now_ms()),
                ),
            )

    async def _create_result(self, run_id: RunId, tenant: TenantId, agent_id: AgentId,
                            payload: Any, artifact_key: Any,
                            state: RunState | None) -> None:
        if (payload is None) == (artifact_key is None):
            # D31 XOR violated upstream; refuse to persist a malformed result.
            logger.error(
                "[agentswarm][swarm] refusing result agent=%s: payload/artifact_key not XOR",
                agent_id,
            )
            return
        if await run_blocking(
            self._result_repo.get_by_run_agent, run_id, agent_id, tenant,
        ) is not None:
            # D24: exactly-once side effects — never duplicate a persisted result.
            return
        await run_blocking(self._result_repo.create, AgentResult(
            id=ResultId(uuid.uuid4().hex),
            run_id=run_id,
            tenant=tenant,
            source_agent_id=agent_id,
            payload=payload,
            artifact_key=artifact_key,
            routed_at=EpochMillis(_now_ms()),
        ))
        if state is not None:
            state.routed_result_count += 1

    def _new_message(self, run_id: RunId, tenant: TenantId, agent_id: AgentId,
                     role: MessageRole, content: Any,
                     token_usage: Any = None) -> Message:
        return Message(
            id=MessageId(uuid.uuid4().hex),
            run_id=run_id,
            tenant=tenant,
            agent_id=agent_id,
            seq=self.next_seq(),
            role=role,
            content=content,
            token_usage=token_usage,
        )

    def create_message(self, run_id: RunId, tenant: TenantId, agent_id: AgentId,
                      role: MessageRole, content: Any,
                      token_usage: Any = None) -> None:
        # Sync path: called from the orchestrator's on_llm_exchange
        # callback, which runs on the loop thread and cannot await.
        self._message_repo.create(
            self._new_message(run_id, tenant, agent_id, role, content,
                              token_usage),
        )

    def record_llm_exchange(self, run_id: RunId, tenant: TenantId,
                            user_prompt: str, decision_dict: dict[str, Any],
                            token_usage: Any) -> None:
        if self._lost_probe():
            return
        orchestrator_id = AgentId(run_id)
        self.create_message(run_id, tenant, orchestrator_id, MessageRole.USER,
                            user_prompt)
        self.create_message(run_id, tenant, orchestrator_id, MessageRole.DECISION,
                            decision_dict, token_usage=token_usage)

    def apply_run_retention_blocking(self, run_id: RunId, tenant: TenantId) -> None:
        # D13: on terminal, keep the per-agent tail plus result-referenced rows.
        # Sync core: the worker's force_stuck/force_fail run on a to_thread
        # worker thread already, off the event loop.
        tail = self._config.message_retention_tail
        if tail <= 0:
            return
        for agent in self._agent_repo.list_by_run(run_id, tenant):
            self._message_repo.apply_retention(run_id, agent.agent_id, tenant, tail)
        self._message_repo.apply_retention(run_id, AgentId(run_id), tenant, tail)

    async def apply_run_retention(self, run_id: RunId, tenant: TenantId) -> None:
        await run_blocking(self.apply_run_retention_blocking, run_id, tenant)

    async def unsatisfied_refs(self, run_id: RunId, tenant: TenantId,
                                refs: tuple[str, ...]) -> tuple[str, ...]:
        result_names = set()
        for agent in await run_blocking(self._agent_repo.list_by_run, run_id, tenant):
            if await run_blocking(
                self._result_repo.get_by_run_agent,
                run_id, agent.agent_id, tenant,
            ) is not None:
                result_names.add(agent.name)
        return tuple(ref for ref in refs if ref not in result_names)

    async def first_failed_wait_ref(self, run_id: RunId, tenant: TenantId,
                                    refs: tuple[str, ...]) -> dict[str, Any] | None:
        """The first unsatisfiable wait ref: its agent is terminal FAILED.

        An agent blocks a wait ref when its row is terminal FAILED and no
        result exists for it — FAILED rows never grow a result
        (``persist_completion``), so every such ref can only be satisfied
        by a spawn that will never be scheduled (no respawn/supersede
        exists). The one exception is ``error='slice_lost'``: that FAILED
        row is a reclaimed claim (a transient reclaim artifact, not a dead
        branch), and ``rehydrate`` revives it so the rescheduled slice
        respawns the agent under its D16 identity — the ref stays
        satisfiable and must not fail the run. A result-checked
        COMPLETE-side agent and any non-FAILED agent stay quiet: a dead
        optional branch must not kill a would-succeed run.

        Used by BOTH the in-slice wait (`SwarmRunHandler._await_refs`)
        and the rehydrate prologue, so the in-slice wedge and the
        post-crash wedge die through the same predicate. Returns
        ``{"agent_id", "name", "attempts", "error"}`` of the first
        failed-blocking agent, else None.
        """
        if not refs:
            return None
        wanted = set(refs)
        for agent in await run_blocking(
                self._agent_repo.list_by_run, run_id, tenant):
            if (agent.name not in wanted
                    or agent.status is not AgentStatus.FAILED):
                continue
            if agent.error == _SLICE_LOST_ERROR:
                # A slice_lost failure is a reclaimed claim, not a dead
                # branch: the rescheduled slice respawns the agent under
                # its D16 identity (see rehydrate), so this ref is still
                # satisfiable and must not fail the run.
                continue
            if await run_blocking(
                self._result_repo.get_by_run_agent,
                run_id, agent.agent_id, tenant,
            ) is not None:
                continue
            return {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "attempts": agent.attempts,
                "error": agent.error,
            }
        return None

    async def first_slice_lost_wait_ref(self, run_id: RunId, tenant: TenantId,
                                        refs: tuple[str, ...]) -> dict[str, Any] | None:
        """The first unsatisfied wait ref whose agent is a resultless FAILED
        ``slice_lost`` row — the in-slice-wait deadlock signal.

        Mirror of ``first_failed_wait_ref`` with the ONE predicate inverted:
        fires ONLY on ``error == 'slice_lost'`` resultless FAILED rows. Such a
        row is revivable by the *rehydrate prologue* (D16), but the IN-SLICE
        wait can never satisfy it (the respawn re-fences at the current claim
        epoch). Returning this ref lets ``_await_refs`` force an immediate
        reschedule instead of blocking to the slice deadline.

        Returns ``{"agent_id", "name", "attempts", "error"}`` of the first
        resultless FAILED slice_lost agent on a waited ref, else None.
        """
        if not refs:
            return None
        wanted = set(refs)
        for agent in await run_blocking(
                self._agent_repo.list_by_run, run_id, tenant):
            if (agent.name not in wanted
                    or agent.status is not AgentStatus.FAILED
                    or agent.error != _SLICE_LOST_ERROR):
                continue
            if await run_blocking(
                self._result_repo.get_by_run_agent,
                run_id, agent.agent_id, tenant,
            ) is not None:
                continue
            return {
                "agent_id": agent.agent_id,
                "name": agent.name,
"attempts": agent.attempts,
                    "error": agent.error,
                }
        return None

    async def unspawned_wait_refs(self, run_id: RunId, tenant: TenantId,
                                  refs: tuple[str, ...]) -> tuple[str, ...]:
        """Refs that name NO agent row at all — a spawn deferred by the
        width/depth cap this slice (or a name awaited ahead of its spawn).
        Not failed, not lost: a pure deferral the next turn resolves."""
        if not refs:
            return ()
        known = {
            a.name for a in await run_blocking(
                self._agent_repo.list_by_run, run_id, tenant)
        }
        return tuple(r for r in refs if r not in known)

    def get_results(self, run_id: RunId, tenant: TenantId) -> list[dict[str, Any]]:
        # Sync path: the orchestrator invokes result_getter as a sync
        # callback on the loop thread.
        agents = {a.agent_id: a.name for a in self._agent_repo.list_by_run(run_id, tenant)}
        results = self._result_repo.list_by_run(run_id, tenant)
        return [
            {
                "agent": agents.get(s.source_agent_id, s.source_agent_id),
                "result": s.payload if s.payload is not None else f"artifact://{s.artifact_key}",
            }
            for s in results
        ]

    async def rehydrate(self, run: Run, claim_epoch: int = 0) -> tuple[RunState, set[tuple[str, str]], list[Agent]]:
        state = RunState(run=run)
        state.round_index = len(
            await run_blocking(self._round_repo.list_by_run, run.run_id, run.tenant)
        )
        agents = await run_blocking(
            self._agent_repo.list_by_run, run.run_id, run.tenant,
        )
        state.total_agents_spawned = len(agents)
        state.routed_result_count = len(
            await run_blocking(self._result_repo.list_by_run, run.run_id, run.tenant)
        )

        done_pairs: set[tuple[str, str]] = set()
        interrupted: list[Agent] = []
        for agent in agents:
            result = await run_blocking(
                self._result_repo.get_by_run_agent,
                run.run_id, agent.agent_id, run.tenant,
            )
            if result is None:
                # A FAILED slice_lost row is a reclaim artifact the peer
                # must respawn; a FAILED row with any other error stays
                # terminal (genuine failure -> run fails, no wedge).
                revivable = (
                    agent.status in (AgentStatus.QUEUED, AgentStatus.RUNNING)
                    or (agent.status is AgentStatus.FAILED
                        and agent.error == _SLICE_LOST_ERROR)
                )
                if revivable:
                    if agent.status is AgentStatus.FAILED:
                        if self._attempt_repo is not None:
                            burned = await run_blocking(
                                self._attempt_repo.count_agent_provisionings,
                                run.run_id, run.tenant, agent.agent_id,
                            )
                            if burned >= self._config.orchestration.agent_max_attempts:
                                # A poison agent that already burned its
                                # whole provisioning budget (the ledger rows
                                # ARE the DB-recorded retry history) must
                                # not mint yet another persona after a
                                # reclaim — stay FAILED so the wait's
                                # fail-fast terminates the run instead of
                                # respawning forever.
                                logger.warning(
                                    "[agentswarm][swarm] revive cap reached "
                                    "agent=%s name=%s burned=%d max=%d",
                                    agent.agent_id, agent.name, burned,
                                    self._config.orchestration.agent_max_attempts,
                                )
                                continue
                        # Un-poison the row before this slice waits on it:
                        # left FAILED, the in-slice wait classifies the very
                        # ref this slice respawned as resultless slice_lost,
                        # fires the reschedule sentinel on it, and loops the
                        # run to D40 before the respawn can complete. Flip
                        # the row back to a RUNNING in-flight state under
                        # the claim fence (same fenced idempotent-write
                        # shape as the result-present QUEUED->COMPLETED
                        # flip); error=None is update_status's
                        # leave-unchanged contract — the status flip is
                        # what un-classifies the ref.
                        reset = await run_blocking(
                            self._agent_repo.update_status,
                            run.run_id, agent.agent_id, run.tenant,
                            AgentStatus.RUNNING.value,
                            attempts=0, error=None, claim_epoch=claim_epoch,
                        )
                        if not reset:
                            # A stale epoch means this slice must not own
                            # the respawn; the row stays FAILED for the
                            # current claim owner to revive.
                            logger.info(
                                "[agentswarm][swarm] stale claim, revival "
                                "skipped agent=%s claim_epoch=%d",
                                agent.agent_id, claim_epoch,
                            )
                            continue
                    interrupted.append(agent)
                continue
            done_pairs.add((agent.name, agent.task_spec.objective))
            if agent.status in (AgentStatus.QUEUED, AgentStatus.RUNNING):
                # D24: finished before a crash — flip COMPLETED idempotently,
                # never re-enqueue.
                await run_blocking(
                    self._agent_repo.update_status,
                    run.run_id, agent.agent_id, run.tenant,
                    AgentStatus.COMPLETED.value,
                    output=agent_output(
                        result.payload if result.payload is not None else None,
                        result.artifact_key,
                    ),
                    claim_epoch=claim_epoch,
                )

        # Spawn history for still-open agents so the orchestrator's dedup guard
        # cannot replay an objective that is already in flight (D34: rebuilt
        # from durable rows, not kept in memory). ALL rows contribute — a
        # finished (COMPLETED) row's objective must stay in the dedup set so
        # an already-done name is never re-spawned as a duplicate row.
        state.last_spawn_objectives = [
            a.task_spec.objective for a in agents
        ]

        orchestrator_msgs = await run_blocking(
            self._message_repo.list_by_agent,
            run.run_id, AgentId(run.run_id), run.tenant,
        )
        for msg in orchestrator_msgs:
            if not isinstance(msg.content, dict) or msg.role != MessageRole.DECISION:
                continue
            state.turn_count += 1
            # The tail DECISION message is the slice's live wait intent
            # (any later decision supersedes it); when the last decision a
            # crashed slice persisted was a wait, those refs are what the
            # resumed slice owes durable work to.
            if msg.content.get("kind") == DecisionKind.WAIT.value:
                state.pending_wait_refs = tuple(
                    str(ref) for ref in (msg.content.get("refs") or ()))
            else:
                state.pending_wait_refs = ()
            if msg.content.get("kind") == "handoff":
                target = msg.content.get("target")
                for spec in run.agent_catalog:
                    if spec.name == target or spec.role == target:
                        state.active_persona = spec
                        break
        return state, done_pairs, interrupted