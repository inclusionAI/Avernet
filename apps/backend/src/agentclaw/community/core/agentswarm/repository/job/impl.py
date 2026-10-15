"""JobRepository — ac_agent_swarm_job durable queue persistence (D19 no-await-in-session)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, delete, func, select, update
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.job import model as om
from agentclaw.community.core.agentswarm.repository.job.protocol import (
    JobRepositoryProtocol,
)
from agentclaw.community.core.agentswarm.repository.shard import model as om_shard
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

TaskQueueStatus = dm.TaskQueueStatus

# Job statuses the claim/reclaim predicates can never match again: no
# worker will act on the run through the queue. Anything else (PENDING,
# RUNNING — live lease or expired) is reachable and owned by the queue.
_DEAD_JOB_STATUSES = (
    TaskQueueStatus.SUCCEEDED.value,
    TaskQueueStatus.FAILED.value,
    TaskQueueStatus.TIMED_OUT.value,
)


def _routed_shard_count():
    """Correlated scalar subquery: the run's live routed-shard count.

    4.5 (progress-aware breaker): the count is evaluated per updated job
    row, DB-side, so the marker write keeps the same UPDATE-is-the-claim
    CAS discipline as the epoch/reclaim increments — no read-then-write
    window for racing pods to interleave in. Correlation on run_id only:
    run_id is globally unique, so no env/tenant join is needed.
    """
    return (
        select(func.count())
        .select_from(om_shard.AcAgentSwarmResultShardModel)
        .where(
            om_shard.AcAgentSwarmResultShardModel.run_id
            == om.AcAgentSwarmJobModel.run_id,
            om_shard.AcAgentSwarmResultShardModel.is_deleted.is_(False),
        )
        .scalar_subquery()
    )


def _utcnow() -> datetime:
    # utcnow() is deprecated (3.12): aware-now, stripped to naive UTC so the
    # job columns keep their naive-UTC storage convention (SQLite compares
    # datetimes as text; mixing aware and naive renders would drift).
    return datetime.now(timezone.utc).replace(tzinfo=None)



def _new_id() -> str:
    return uuid.uuid4().hex


class JobRepository(JobRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def enqueue(self, run_id: dm.RunId, tenant: dm.TenantId,
                deadline_seconds: int, run_at: datetime | None = None) -> None:
        # No return (D19): orm_session commits with expire_on_commit=True, so
        # a returned instance would be detached+expired (DetachedInstanceError
        # on attribute access); claim_batch is the read path.
        with self._db.orm_session() as session:
            row = om.AcAgentSwarmJobModel(
                job_id=_new_id(),
                run_id=run_id,
                status=TaskQueueStatus.PENDING.value,
                run_at=run_at or _utcnow(),
                deadline_at=_utcnow() + timedelta(seconds=deadline_seconds),
                tenant=tenant,
            )
            session.add(row)

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                )
            )
        return result.rowcount

    def claim_batch(self, worker_id: str, tenant_filter: list[dm.TenantId] | None,
                    limit: int, lease_seconds: int) -> list[dict]:
        """claim predicate: env + PENDING + run_at due.

        Deadline-blind on purpose (4.4): `deadline_at` mirrors the
        submit-sync wait budget, not the run's wall clock, and a
        past-deadline PENDING job must stay claimable so the
        rehydrated slice's TerminationEvaluator fires the fenced
        TIMED_OUT through the normal path — never filtered here.
        """
        now = _utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._db.orm_session() as session:
            stmt = (
                select(om.AcAgentSwarmJobModel.id).where(
                    # env first: binds the env-leading indexes (idx_job_scope_*)
                    om.AcAgentSwarmJobModel.env == get_current_env(),
                    om.AcAgentSwarmJobModel.status == TaskQueueStatus.PENDING.value,
                    om.AcAgentSwarmJobModel.run_at <= now,
                    om.AcAgentSwarmJobModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmJobModel.id).limit(limit)
            )
            if tenant_filter:
                stmt = stmt.where(om.AcAgentSwarmJobModel.tenant.in_([str(t) for t in tenant_filter]))
            candidate_ids = [r[0] for r in session.execute(stmt).all()]
            claimed = []
            for id_ in candidate_ids:
                # Selecting candidates then claiming them per-row is a race by
                # construction under more than one pod: SELECT-then-flip lets
                # two workers both see the same PENDING rows, mint the same
                # next epoch, and both walk away as the owner. The claim is
                # the UPDATE instead: increments are DB-side, and the stale
                # predicate (status must STILL be PENDING) means whoever
                # commits first moves the row; a loser's UPDATE matches
                # nothing and the job is not theirs.
                result = session.execute(
                    update(om.AcAgentSwarmJobModel).where(
                        om.AcAgentSwarmJobModel.id == id_,
                        om.AcAgentSwarmJobModel.env == get_current_env(),
                        om.AcAgentSwarmJobModel.status == TaskQueueStatus.PENDING.value,
                        om.AcAgentSwarmJobModel.is_deleted.is_(False),
                    ).values(
                        status=TaskQueueStatus.RUNNING.value,
                        claimed_by=worker_id,
                        claim_epoch=om.AcAgentSwarmJobModel.claim_epoch + 1,
                        lease_expires_at=lease_until,
                        attempts=om.AcAgentSwarmJobModel.attempts + 1,
                        # 4.5 baseline: a claim observes the run's current
                        # routed-shard count into progress_marker, replacing
                        # the -1 sentinel. Fresh baseline per claim start —
                        # D40 comparisons only make sense BETWEEN attempts.
                        progress_marker=_routed_shard_count(),
                    )
                )
                if result.rowcount != 1:
                    continue  # a racing pod claimed it first: not ours
                row = session.execute(
                    select(om.AcAgentSwarmJobModel).where(
                        om.AcAgentSwarmJobModel.id == id_)
                ).scalar_one()
                claimed.append({
                    "run_id": dm.RunId(row.run_id),
                    "tenant": dm.RunId(row.tenant) if False else dm.TenantId(row.tenant),
                    "claim_epoch": row.claim_epoch,
                    "attempts": row.attempts,
                    "cancel_requested": bool(row.cancel_requested),
                    "deadline_at": row.deadline_at,
                    "progress_marker": row.progress_marker,
                })
            return claimed

    def reclaim_batch(self, worker_id: str, limit: int, lease_seconds: int) -> list[dict]:
        """Re-claim RUNNING jobs whose lease expired (crashed pod / lost worker).

        Returns claim dicts extended with `reclaims`; the worker applies the
        D40 circuit breaker: `reclaims` > reclaim_max -> run terminal STUCK.
        The claim is the UPDATE — DB-side increments behind the still-expired
        predicate — so two reclaimers racing on the same dead pod converge on
        one owner instead of minting twin epochs.

        Deadline-blind on purpose (4.4): a past-deadline row must stay
        reclaimable so the rehydrated slice's wall-clock fires the fenced
        TIMED_OUT itself.

        4.5 (progress-aware breaker): the same UPDATE compares the run's
        current routed-shard count against the stored `progress_marker` —
        growth resets `reclaims` (rolling restarts no longer trip D40), a
        flat count increments exactly as before (genuine crash-idle loops
        still STUCK). The -1 sentinel (mixed version / never claimed by a
        new replica) is the *first observation* case: it becomes the
        baseline and is never classified as progress. All SET expressions
        evaluate against the pre-update row, so the compare-and-branch
        stays one atomic DB-side CAS.
        """
        now = _utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmJobModel.id).where(
                    om.AcAgentSwarmJobModel.env == get_current_env(),
                    om.AcAgentSwarmJobModel.status == TaskQueueStatus.RUNNING.value,
                    om.AcAgentSwarmJobModel.lease_expires_at < now,
                    om.AcAgentSwarmJobModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmJobModel.id).limit(limit)
            ).all()
            reclaimed = []
            for (id_,) in rows:
                result = session.execute(
                    update(om.AcAgentSwarmJobModel).where(
                        om.AcAgentSwarmJobModel.id == id_,
                        om.AcAgentSwarmJobModel.env == get_current_env(),
                        om.AcAgentSwarmJobModel.status == TaskQueueStatus.RUNNING.value,
                        om.AcAgentSwarmJobModel.lease_expires_at < now,
                        om.AcAgentSwarmJobModel.is_deleted.is_(False),
                    ).values(
                        claimed_by=worker_id,
                        claim_epoch=om.AcAgentSwarmJobModel.claim_epoch + 1,
                        lease_expires_at=lease_until,
                        reclaims=case(
                            (
                                and_(
                                    om.AcAgentSwarmJobModel.progress_marker != -1,
                                    _routed_shard_count()
                                    > om.AcAgentSwarmJobModel.progress_marker,
                                ),
                                0,
                            ),
                            else_=om.AcAgentSwarmJobModel.reclaims + 1,
                        ),
                        attempts=om.AcAgentSwarmJobModel.attempts + 1,
                        progress_marker=_routed_shard_count(),
                    )
                )
                if result.rowcount != 1:
                    continue  # another reclaimer won it; the loser
                    # re-identifies nothing: lease got rewrote to the future
                row = session.execute(
                    select(om.AcAgentSwarmJobModel).where(
                        om.AcAgentSwarmJobModel.id == id_)
                ).scalar_one()
                reclaimed.append({
                    "run_id": dm.RunId(row.run_id),
                    "tenant": dm.TenantId(row.tenant),
                    "claim_epoch": row.claim_epoch,
                    "attempts": row.attempts,
                    "reclaims": row.reclaims,
                    "cancel_requested": bool(row.cancel_requested),
                    "deadline_at": row.deadline_at,
                    "progress_marker": row.progress_marker,
                })
            return reclaimed

    def renew_lease(self, run_id: dm.RunId, tenant: dm.TenantId,
                    worker_id: str, lease_seconds: int, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                    om.AcAgentSwarmJobModel.claimed_by == worker_id,
                    om.AcAgentSwarmJobModel.claim_epoch == claim_epoch,
                    om.AcAgentSwarmJobModel.is_deleted.is_(False),
                ).values(lease_expires_at=_utcnow() + timedelta(seconds=lease_seconds))
            )
        return result.rowcount > 0

    def reschedule(self, run_id: dm.RunId, tenant: dm.TenantId, worker_id: str,
                   delay_seconds: float, error: str | None, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                    om.AcAgentSwarmJobModel.claimed_by == worker_id,
                    om.AcAgentSwarmJobModel.claim_epoch == claim_epoch,
                ).values(
                    status=TaskQueueStatus.PENDING.value,
                    run_at=_utcnow() + timedelta(seconds=delay_seconds),
                    last_error=error,
                    claimed_by=None,
                    lease_expires_at=None,
                )
            )
        return result.rowcount > 0

    def complete(self, run_id: dm.RunId, tenant: dm.TenantId,
                 worker_id: str, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                    om.AcAgentSwarmJobModel.claimed_by == worker_id,
                    om.AcAgentSwarmJobModel.claim_epoch == claim_epoch,
                ).values(status=TaskQueueStatus.SUCCEEDED.value, claimed_by=None, lease_expires_at=None)
            )
        return result.rowcount > 0

    def fail(self, run_id: dm.RunId, tenant: dm.TenantId, error: str,
             worker_id: str, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                    om.AcAgentSwarmJobModel.claimed_by == worker_id,
                    om.AcAgentSwarmJobModel.claim_epoch == claim_epoch,
                ).values(status=TaskQueueStatus.FAILED.value, last_error=error, claimed_by=None, lease_expires_at=None)
            )
        return result.rowcount > 0

    def request_cancel(self, run_id: dm.RunId, tenant: dm.TenantId) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                    om.AcAgentSwarmJobModel.is_deleted.is_(False),
                ).values(cancel_requested=True)
            )
        return result.rowcount > 0

    def count_pending_jobs(self) -> int:
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.env == get_current_env(),
                    om.AcAgentSwarmJobModel.status == TaskQueueStatus.PENDING.value,
                    om.AcAgentSwarmJobModel.is_deleted.is_(False),
                )
            ).scalar_one()

    def get_run_job(self, run_id: dm.RunId, tenant: dm.TenantId) -> dict | None:
        """Reaper probe (4.1): the run's single job row, or None.

        Interpretation is the caller's (the maintenance loop) and follows
        the claim predicates for consistency: a PENDING row is claimable
        once run_at falls due, a RUNNING row is reachable whether its
        lease lives (an owner holds it) or expired (reclaim_batch owns
        it, and the D40 breaker turns dead loops terminal) — only the
        _DEAD_JOB_STATUSES values leave the run unreachable from the
        queue, which is exactly the reaper's orphan definition.
        """
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmJobModel).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                    om.AcAgentSwarmJobModel.env == get_current_env(),
                    om.AcAgentSwarmJobModel.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "status": row.status,
                "claim_epoch": row.claim_epoch,
                "deadline_at": row.deadline_at,
                "lease_expires_at": row.lease_expires_at,
                "reclaims": row.reclaims,
                "progress_marker": row.progress_marker,
            }

    def requeue_orphan(self, run_id: dm.RunId, tenant: dm.TenantId,
                       deadline_seconds: float) -> bool:
        """Reaper re-enqueue (4.1): ensure a claimable PENDING row exists
        for a RUNNING run whose job row is dead or missing.

        uk_job_run is the convergence point for concurrent reapers and
        for the submitter's own enqueue: this INSERT either wins (fresh
        row, returns True) or collides. On collision the surviving row is
        revived ONLY when it is terminal — a live PENDING/RUNNING row is
        owned by claim/reclaim and never touched here (returns False).
        Revival bumps claim_epoch, so terminal-era writes fenced at the
        dead row's epoch can never land against the revived row, and
        resets the counters so the revival behaves like a fresh submit:
        progress_marker back to the -1 unobserved sentinel.
        """
        now = _utcnow()
        try:
            with self._db.orm_session() as session:
                session.add(om.AcAgentSwarmJobModel(
                    job_id=_new_id(),
                    run_id=run_id,
                    tenant=tenant,
                    status=TaskQueueStatus.PENDING.value,
                    run_at=now,
                    deadline_at=now + timedelta(seconds=deadline_seconds),
                ))
            return True
        except IntegrityError:
            with self._db.orm_session() as session:
                result = session.execute(
                    update(om.AcAgentSwarmJobModel).where(
                        om.AcAgentSwarmJobModel.run_id == run_id,
                        om.AcAgentSwarmJobModel.tenant == tenant,
                        om.AcAgentSwarmJobModel.env == get_current_env(),
                        om.AcAgentSwarmJobModel.status.in_(_DEAD_JOB_STATUSES),
                        om.AcAgentSwarmJobModel.is_deleted.is_(False),
                    ).values(
                        status=TaskQueueStatus.PENDING.value,
                        run_at=now,
                        deadline_at=now + timedelta(seconds=deadline_seconds),
                        claimed_by=None,
                        lease_expires_at=None,
                        attempts=0,
                        reclaims=0,
                        progress_marker=-1,
                        last_error=None,
                        claim_epoch=om.AcAgentSwarmJobModel.claim_epoch + 1,
                    )
                )
            return result.rowcount == 1

    def is_cancel_requested(self, run_id: dm.RunId, tenant: dm.TenantId) -> bool:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmJobModel.cancel_requested).where(
                    om.AcAgentSwarmJobModel.run_id == run_id,
                    om.AcAgentSwarmJobModel.tenant == tenant,
                    om.AcAgentSwarmJobModel.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            return bool(row) if row is not None else False