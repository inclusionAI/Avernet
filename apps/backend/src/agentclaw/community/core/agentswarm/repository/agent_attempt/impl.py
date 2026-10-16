"""AgentAttemptRepository — ac_agent_swarm_agent_attempt persistence
(D19 no-await-in-session).

The per-attempt executor ledger (D-K): one row per provisioning success,
keyed by (env, tenant, run, agent, claim epoch, attempt). Written by the
pool through the D28 claim fence (a stale slice's journal insert is
inert), read by the terminal destroy sweep, and marked DESTROYED per
ledger row as personas are cleaned.
"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.agent_attempt import model as om
from agentclaw.community.core.agentswarm.repository.job import model as om_job
from agentclaw.community.core.agentswarm.repository.agent_attempt.protocol import (
    AgentAttemptRepositoryProtocol,
)
from agentclaw.community.core.agentswarm.repository.types import _now_ms
from agentclaw.community.plugin_api.database import DatabasePlugin

EXECUTOR_STATE_SPAWNED = "SPAWNED"
EXECUTOR_STATE_DESTROYED = "DESTROYED"


class AgentAttemptRepository(AgentAttemptRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def record_agent_binding(self, run_id: dm.RunId, tenant: dm.TenantId,
                              agent_id: dm.AgentId, claim_epoch: int,
                              attempt: int, executor_kind: str,
                              executor_id: str, executor_name: str) -> bool:
        """Fence-gated journal insert (D28), the update_status pattern.

        The insert is allowed only while the job row's CURRENT claim_epoch
        matches the caller's — the same EXISTS predicate AgentRepository.
        update_status applies to its UPDATE, evaluated in the same
        session/transaction as the insert. A stale slice's write is inert
        (rowcount-gate returns False, nothing lands). A UK replay — the
        same (env, tenant, run, agent, epoch, attempt) row already
        journaled — converges to True instead of surfacing the race
        (idempotent journal, requeue_orphan's convergence idiom).
        """
        try:
            with self._db.orm_session() as session:
                claim_alive = session.execute(
                    select(om_job.AcAgentSwarmJobModel.job_id).where(
                        om_job.AcAgentSwarmJobModel.run_id == run_id,
                        om_job.AcAgentSwarmJobModel.tenant == tenant,
                        om_job.AcAgentSwarmJobModel.claim_epoch == claim_epoch,
                        om_job.AcAgentSwarmJobModel.is_deleted.is_(False),
                    )
                ).first()
                if claim_alive is None:
                    return False
                session.add(om.AcAgentSwarmAgentAttemptModel(
                    agent_id=agent_id,
                    run_id=run_id,
                    tenant=tenant,
                    claim_epoch=claim_epoch,
                    attempt=attempt,
                    executor_kind=executor_kind,
                    executor_id=executor_id,
                    executor_name=executor_name,
                    executor_state=EXECUTOR_STATE_SPAWNED,
                    started_at=_now_ms(),
                ))
        except IntegrityError:
            # uk_agent_attempt replay: the row already landed — the ledger
            # is idempotent, converge instead of surfacing the collision
            # (same discipline as JobRepository.requeue_orphan).
            return True
        return True

    def list_agent_bindings(self, run_id: dm.RunId,
                             tenant: dm.TenantId) -> list[dict]:
        """Every live ledger row of the run, ascending insertion order.

        Rows are NOT pre-deduped by executor_id: a cross-slice replay of
        one attempt number (crash-resume adopt) journals a NEW row per
        claim epoch, so the same executor id legitimately appears twice —
        the destroy sweep dedupes delete calls itself while this list
        keeps both rows addressable (crash-orphan traceability, D-K).
        """
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmAgentAttemptModel).where(
                    om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                    om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                    om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmAgentAttemptModel.id)
            ).scalars().all()
            return [
                {
                    "agent_id": r.agent_id,
                    "claim_epoch": r.claim_epoch,
                    "attempt": r.attempt,
                    "executor_kind": r.executor_kind,
                    "executor_id": r.executor_id,
                    "executor_name": r.executor_name,
                    "executor_state": r.executor_state,
                    "started_at": r.started_at,
                }
                for r in rows
            ]

    def mark_binding_destroyed(self, run_id: dm.RunId, tenant: dm.TenantId,
                               executor_id: str) -> int:
        """Mark every live row of one executor DESTROYED (per ledger row).

        executor_id-level addressing across the whole run: one persona id
        can sit on more than one row (the cross-slice replay case above),
        and the sweep marks each of them as its own record. Post-terminal
        by contract — no claim fence applies (the job row is already
        complete); rows already marked stay untouched, so the count is
        the rows this call transitioned.
        """
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmAgentAttemptModel).where(
                    om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                    om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                    om.AcAgentSwarmAgentAttemptModel.executor_id == executor_id,
                    om.AcAgentSwarmAgentAttemptModel.executor_state
                    != EXECUTOR_STATE_DESTROYED,
                    om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                ).values(executor_state=EXECUTOR_STATE_DESTROYED)
            )
            return result.rowcount