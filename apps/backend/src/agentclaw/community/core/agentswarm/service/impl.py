"""agentswarm concrete service — runs the swarm dispatch lifecycle."""
from __future__ import annotations

import asyncio
import json
from agentclaw.community.log import get_logger
import time
import uuid

from agentclaw.community.core.agentswarm.config.settings import AgentSwarmConfig
from agentclaw.community.core.agentswarm.domain.models import (
    Agent, AgentKind, AgentStatus, BotState, EpochMillis, Run, RunId,
    RunPage, RunStatus, TenantId,
)
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError, AgentSwarmRunNotFoundError,
    AgentSwarmRunNotPurgeableError, AgentSwarmServiceProtocol,
    AgentSwarmSyncTimeoutError, AgentAuthorizationState, AgentDetailItem,
    AgentEvent, AgentMessageDetail, AgentResultDetail, AgentAttemptDetail,
    BotAuthorizationDescriptor, BotAuthorizationSource,
    GlobalRunLimitExceeded, GuardEventDetailItem, GuardEventSummary,
    QueueDepthExceeded, RoundDetailItem, RoundEvent, RunDetail, RunEvents,
    SubmitRunRequest, TenantRunLimitExceeded,
)

logger = get_logger("agentswarm")

# Agents whose own lifecycle is over: a terminal agent's gate can no longer
# be acted on by a human, so it is never read from the authorization source.
_TERMINAL_AGENT_STATUSES = frozenset({
    AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.KILLED,
})


def _unresolvable_authorization(agent: Agent) -> BotAuthorizationDescriptor:
    """The parked gate for a row whose creation payload cannot answer.

    Presence is keyed off the durable park state, so the descriptor still
    surfaces — with the wire module's empty-spelling for payload facts the
    seam could not serve — never a fabricated URL or deadline for it.
    """
    return BotAuthorizationDescriptor(
        state=BotState.AWAITING_AUTHORIZATION.value,
        iframe_url="", redirect_url="", deadline="", application_id="",
    )

class AgentSwarmService(AgentSwarmServiceProtocol):

    def __init__(self, run_repo: RunRepository, dispatch_repo: RunDispatchRepository,
                 agent_repo: AgentRepository, message_repo: MessageRepository,
                 round_repo: RoundRepository, guard_repo: GuardEventRepository,
                 result_repo: AgentResultRepository,
                 config: AgentSwarmConfig,
                 attempt_repo: AgentAttemptRepository | None = None,
                 authorization_source: BotAuthorizationSource | None = None,
    ) -> None:
        self._run_repo = run_repo
        self._dispatch_repo = dispatch_repo
        self._agent_repo = agent_repo
        self._message_repo = message_repo
        self._round_repo = round_repo
        self._guard_repo = guard_repo
        self._result_repo = result_repo
        self._attempt_repo = attempt_repo
        self._authorization_source = authorization_source
        self._config = config
        slice_max = config.orchestration.slice_max_seconds
        # Float deadline with a one-poll-cycle floor: int() truncated a
        # sub-second budget (0.5 -> 0) into an instant submit_sync
        # return, and a zero/absent config must default — never wedge
        # the wait to zero.
        self._deadline: float = max(float(slice_max or 3600.0), 1.0)

    async def submit_async(self, req: SubmitRunRequest) -> str:
        self._require_llm_config()
        if req.idempotency_key:
            existing = self._run_repo.get_by_idempotency(
                req.idempotency_key, TenantId(req.tenant),
            )
            if existing is not None:
                # Admission (12.1) is checked after the idempotency join:
                # a duplicate submit joins an existing run and consumes no
                # new capacity, so saturation must never refuse it.
                return existing.run_id
        limit = self._config.tenant_limits.max_concurrent_runs
        # Known bounded race: count-then-create is not atomic, so
        # concurrent submits can both pass this check and briefly
        # overshoot the cap. A fully atomic admission needs schema work
        # (no new constraint is added here); the run.create()
        # idempotency convergence below keeps duplicate submits from
        # compounding the overshoot into extra rows.
        if self._run_repo.count_active_runs(TenantId(req.tenant)) >= limit:
            raise TenantRunLimitExceeded(tenant=req.tenant, limit=limit)
        global_limit = self._config.admission.max_global_concurrent_runs
        if self._run_repo.count_active_runs_global() >= global_limit:
            raise GlobalRunLimitExceeded(limit=global_limit)
        pending_limit = self._config.admission.max_pending_dispatches
        if self._dispatch_repo.count_pending_dispatches() >= pending_limit:
            raise QueueDepthExceeded(limit=pending_limit)
        run_id = uuid.uuid4().hex
        run = Run(
            run_id=RunId(run_id),
            tenant=TenantId(req.tenant),
            goal=req.goal,
            agent_catalog=req.agents,
            provider=req.provider,
            request_metadata=req.request_metadata,
            status=RunStatus.RUNNING,
            started_at=EpochMillis(int(time.time() * 1000)),
            owner_id=req.owner_id,
            run_options=req.run_options,
        )
        run.active_idempotency_key = req.idempotency_key
        run.run_options.to_metadata(run.request_metadata)
        created = self._run_repo.create(run)
        if created.run_id != RunId(run_id):
            # Lost the submit race: create() converged onto the winner's
            # existing run (uk_run_idem collision). Join it — returning
            # this run_id WITHOUT enqueueing a second dispatch for the same
            # run, exactly like the early get_by_idempotency join above.
            return created.run_id
        try:
            self._dispatch_repo.enqueue(
                run_id=RunId(run_id),
                tenant=TenantId(req.tenant),
                deadline_seconds=self._deadline,
            )
        except Exception as exc:
            # Orphan-run compensation: the run row THIS call just created
            # is already committed RUNNING and holds the tenant admission
            # slot plus the active idempotency key. If enqueue raised, no
            # worker ever owned this run. The common case is that the dispatch
            # row never committed either (DB down / disk full / deadlock
            # rollback), so the D28-fenced update_status would match zero
            # rows and strand the run RUNNING forever; fail_unclaimed is
            # the unfenced submit-plane seam for exactly this reclaim —
            # it never touched a worker-owned run, and refuses to clobber
            # a racing terminal write. The original enqueue error is
            # never swallowed.
            try:
                freed = self._run_repo.fail_unclaimed(
                    RunId(run_id), TenantId(req.tenant),
                    error=f"dispatch enqueue failed: {type(exc).__name__}: "
                          f"{str(exc)[:160]}",
                )
            except Exception:
                logger.warning(
                    "[agentswarm][service] enqueue-failure compensation "
                    "write raised for run=%s tenant=%s",
                    run_id, req.tenant, exc_info=True,
                )
            else:
                if not freed:
                    logger.warning(
                        "[agentswarm][service] enqueue-failure compensation "
                        "matched no RUNNING run for run=%s tenant=%s",
                        run_id, req.tenant,
                    )
            raise
        return run_id

    async def submit_sync(self, req: SubmitRunRequest) -> Run:
        run_id = await self.submit_async(req)
        deadline = time.monotonic() + self._deadline
        run = self.get_run(run_id, req.tenant)
        while time.monotonic() < deadline and (
            run is None or run.status is RunStatus.RUNNING
        ):
            await asyncio.sleep(1.0)
            run = self.get_run(run_id, req.tenant)
        if run is None:
            # The wait expired without a terminal state and the run row
            # is gone (deleted/purged mid-wait): never fabricate a Run —
            # raise so callers fail clear instead of persisting a
            # synthetic TIMED_OUT run that was never written.
            raise AgentSwarmSyncTimeoutError(
                run_id=run_id, deadline=self._deadline,
            )
        return run

    def get_run(self, run_id: str, tenant: str) -> Run | None:
        return self._run_repo.get(RunId(run_id), TenantId(tenant))

    def list_runs(self, tenant: str, status: RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> RunPage:
        return self._run_repo.list_page(
            TenantId(tenant), status, page, page_size,
        )

    async def cancel(self, run_id: str, tenant: str) -> str:
        ok = self._dispatch_repo.request_cancel(RunId(run_id), TenantId(tenant))
        if not ok:
            return "not_found"
        run = self.get_run(run_id, tenant)
        if run is not None and run.status is not RunStatus.RUNNING:
            return run.status.value
        return RunStatus.RUNNING.value

    def run_events(self, run_id: str, tenant: str, after_agent_id: int = 0,
                   after_round_id: int = 0, after_event: int = 0) -> RunEvents:
        rid, tid = RunId(run_id), TenantId(tenant)
        run = self._run_repo.get(rid, tid)
        if run is None:
            raise AgentSwarmRunNotFoundError(run_id)
        agent_rows = self._agent_repo.list_by_run_with_rows(rid, tid)
        agents = tuple(
            AgentEvent(
                agent_id=a.agent_id, name=a.name, status=a.status, kind=a.kind,
                spawn_round=a.spawn_round, row_cursor=row,
            )
            for row, a in agent_rows
            if row > after_agent_id
        )
        agent_authorizations = tuple(
            AgentAuthorizationState(
                agent_id=str(a.agent_id),
                name=a.name,
                status=a.status.value,
                kind=a.kind.value,
                spawn_round=int(a.spawn_round),
                bot_id=str(a.bot_id) if a.bot_id is not None else None,
                bot_state=a.bot_state.value,
                authorization=self._authorization_for(a, run),
            )
            for _, a in agent_rows
            if a.bot_state is not None
        )
        rounds = tuple(
            RoundEvent(
                round_id=r.id, stage_index=r.stage_index,
                cohort_size=len(r.cohort_agent_ids), row_cursor=row,
            )
            for row, r in self._round_repo.list_by_run_with_rows(rid, tid)
            if row > after_round_id
        )
        guards = tuple(
            GuardEventSummary(kind=g.kind, row_cursor=row)
            for row, g in self._guard_repo.list_by_run_with_rows(rid, tid)
            if row > after_event
        )
        terminal = run.status if run.status is not RunStatus.RUNNING else None
        return RunEvents(
            agents=agents, rounds=rounds, guards=guards,
            run_status=run.status, terminal=terminal,
            agent_authorizations=agent_authorizations,
        )

    def get_run_detail(self, run_id: str, tenant: str) -> RunDetail | None:
        rid, tid = RunId(run_id), TenantId(tenant)
        run = self._run_repo.get(rid, tid)
        if run is None:
            return None

        result_by_agent = {
            str(row.source_agent_id): row
            for row in self._result_repo.list_by_run(rid, tid)
        }
        ledger_by_agent: dict[str, list[AgentAttemptDetail]] = {}
        if self._attempt_repo is not None:
            for row in self._attempt_repo.list_agent_bindings(rid, tid):
                ledger_by_agent.setdefault(str(row["agent_id"]), []).append(
                    AgentAttemptDetail(
                        claim_epoch=int(row["claim_epoch"]),
                        slice_retry_index=int(row["slice_retry_index"]),
                        executor_kind=str(row["executor_kind"]),
                        executor_id=str(row["executor_id"]),
                        executor_name=str(row["executor_name"]),
                        binding_state=str(row["binding_state"]),
                        result_status=row["result_status"],
                        result_message=row["result_message"],
                        started_at=row["started_at"],
                    )
                )

        agent_items: list[AgentDetailItem] = []
        for agent in self._agent_repo.list_by_run(rid, tid):
            result_row = result_by_agent.get(str(agent.agent_id))
            result = (
                AgentResultDetail(
                    payload=result_row.payload,
                    artifact_key=(
                        str(result_row.artifact_key)
                        if result_row.artifact_key is not None else None
                    ),
                    routed_at=int(result_row.routed_at),
                )
                if result_row is not None else None
            )
            messages = tuple(
                AgentMessageDetail(
                    message_id=str(m.id),
                    agent_id=str(m.agent_id),
                    seq=int(m.seq),
                    role=m.role.value,
                    content=m.content,
                    decision=m.decision,
                    token_usage=m.token_usage,
                    artifact_key=(
                        str(m.artifact_key)
                        if m.artifact_key is not None else None
                    ),
                    created_at=int(m.created_at),
                )
                for m in self._message_repo.list_by_agent(rid, agent.agent_id, tid)
            )
            agent_items.append(AgentDetailItem(
                agent_id=str(agent.agent_id),
                name=agent.name,
                role=agent.role,
                kind=agent.kind.value,
                status=agent.status.value,
                parent_agent_id=(
                    str(agent.parent_agent_id)
                    if agent.parent_agent_id is not None else None
                ),
                depth=int(agent.depth),
                spawn_round=int(agent.spawn_round),
                idempotency_key=str(agent.idempotency_key),
                attempts=int(agent.attempts),
                bot_id=str(agent.bot_id) if agent.bot_id is not None else None,
                bot_state=(
                    agent.bot_state.value if agent.bot_state is not None else None
                ),
                error=agent.error,
                output=agent.output,
                started_at=(
                    int(agent.started_at) if agent.started_at is not None else None
                ),
                ended_at=(
                    int(agent.ended_at) if agent.ended_at is not None else None
                ),
                result=result,
                attempt_ledger=tuple(ledger_by_agent.get(str(agent.agent_id), ())),
                messages=messages,
                authorization=self._authorization_for(agent, run),
            ))

        rounds = tuple(
            RoundDetailItem(
                round_id=str(row.id),
                stage_index=int(row.stage_index),
                orchestrator_steps=int(row.orchestrator_steps),
                slowest_attempt_steps=int(row.slowest_attempt_steps),
                cohort_agent_ids=tuple(str(a) for a in row.cohort_agent_ids),
                started_at=int(row.started_at),
                ended_at=(
                    int(row.ended_at) if row.ended_at is not None else None
                ),
            )
            for row in self._round_repo.list_by_run(rid, tid)
        )
        guards = tuple(
            GuardEventDetailItem(
                event_id=str(row.id),
                agent_id=(
                    str(row.agent_id) if row.agent_id is not None else None
                ),
                kind=row.kind.value,
                details=row.details,
                created_at=int(row.created_at),
            )
            for row in self._guard_repo.list_by_run(rid, tid)
        )
        return RunDetail(
            run_id=str(run.run_id),
            tenant=tenant,
            goal=run.goal,
            status=run.status.value,
            termination_reason=(
                run.termination_reason.value
                if run.termination_reason is not None else None
            ),
            provider=run.provider.value,
            final_answer=run.final_answer,
            error=run.error,
            started_at=(
                int(run.started_at) if run.started_at is not None else None
            ),
            ended_at=int(run.ended_at) if run.ended_at is not None else None,
            agent_catalog=tuple(run.agent_catalog),
            agents=tuple(agent_items),
            rounds=rounds,
            guards=guards,
        )

    def _require_llm_config(self) -> None:
        llm = self._config.llm
        if llm is None or not llm.base_url:
            raise AgentSwarmConfigError(
                "agentswarm llm config missing: base_url/api_key required"
            )

    def _authorization_for(
        self, agent: Agent, run: Run,
    ) -> BotAuthorizationDescriptor | None:
        """The pending gate as the authorization source answers it.

        Presence keys off the live application's status, not the agent
        row's cached ``bot_state`` write: multiple bot agents spawn in
        parallel and every application goes PENDING at once, but the
        provisioning pool reaches — and parks — each agent's row
        sequentially, so a later agent's window can already be running
        (or lapse) while its row still carries a stale or absent state.
        Every non-terminal bot-kind agent is therefore read through the
        source, whose answer distinguishes PENDING (descriptor) from
        issued/approved/rejected/expired (no gate). The row's parked
        write stays authoritative only for the degraded spellings: a
        durably parked agent surfaces the gate — with the wire module's
        empty facts — even when the source cannot answer for it.
        """
        if agent.kind is not AgentKind.BOT:
            # The gate is a bot-provisioning fact; llm-kind agents never
            # carry a passport application, so they never touch the source.
            return None
        if agent.status in _TERMINAL_AGENT_STATUSES:
            # A terminal agent's provisioning outcome is settled; its
            # detail response stays exactly as before this change.
            return None
        parked = agent.bot_state is BotState.AWAITING_AUTHORIZATION
        if (
            self._authorization_source is None
            or not (run.owner_id or "").strip()
            or agent.bot_id is None
        ):
            return _unresolvable_authorization(agent) if parked else None
        try:
            descriptor = self._authorization_source.authorization(
                owner_id=run.owner_id, bot_id=str(agent.bot_id),
            )
        except Exception:
            logger.warning(
                "[agentswarm][service] authorization payload lookup failed "
                "for agent=%s bot=%s (parked state surfaces without URLs)",
                agent.agent_id, agent.bot_id,
            )
            return _unresolvable_authorization(agent) if parked else None
        if descriptor is None:
            # Not actually pending (or not answerable): only a durably
            # parked row still surfaces the empty-spelling gate.
            return _unresolvable_authorization(agent) if parked else None
        return descriptor

    def purge_run(self, run_id: str, tenant: str) -> dict[str, int] | None:
        rid, tid = RunId(run_id), TenantId(tenant)
        run = self._run_repo.get(rid, tid)
        if run is None:
            return None
        if run.status is RunStatus.RUNNING:
            # Refuse while active: the erasure below is not transactional,
            # so deleting a RUNNING run races an in-flight claimed
            # slice's writes. Cancel or force-fail the run first.
            raise AgentSwarmRunNotPurgeableError(
                run_id=run_id, status=run.status,
            )
        # Hard erasure (12.9): physical deletes across the run's rows.
        # Audit rows are append-only governance records and are kept.
        counts = {
            "agents": self._agent_repo.purge_run(rid, tid),
            "messages": self._message_repo.purge_run(rid, tid),
            "results": self._result_repo.purge_run(rid, tid),
            "rounds": self._round_repo.purge_run(rid, tid),
            "guards": self._guard_repo.purge_run(rid, tid),
            "dispatches": self._dispatch_repo.purge_run(rid, tid),
            "run": self._run_repo.purge_run(rid, tid),
        }
        return counts

    def content_classification(self, run_id: str, tenant: str) -> dict[str, int] | None:
        from agentclaw.community.core.agentswarm.runtime.security import classify_pii
        rid, tid = RunId(run_id), TenantId(tenant)
        run = self._run_repo.get(rid, tid)
        if run is None:
            return None
        totals: dict[str, int] = {}
        for chunk in [run.goal] + [
            message.content if isinstance(message.content, str)
            else json.dumps(message.content, ensure_ascii=False, default=str)
            for agent in self._agent_repo.list_by_run(rid, tid)
            for message in self._message_repo.list_by_agent(rid, agent.agent_id, tid)
        ]:
            for kind in classify_pii(chunk):
                totals[kind.value] = totals.get(kind.value, 0) + 1
        return totals
