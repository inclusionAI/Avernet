"""JobRepository — ac_agent_compute_job durable queue persistence (D19 no-await-in-session)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository.job import model as om
from agentclaw.community.core.agentcompute.repository.job.protocol import (
    JobRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

TaskQueueStatus = dm.TaskQueueStatus


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
                deadline_seconds: int, run_at: datetime | None = None) -> om.AcAgentComputeJobModel:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeJobModel(
                job_id=_new_id(),
                run_id=run_id,
                status=TaskQueueStatus.PENDING.value,
                run_at=run_at or _utcnow(),
                deadline_at=_utcnow() + timedelta(seconds=deadline_seconds),
                tenant=tenant,
            )
            session.add(row)
        return row

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                )
            )
        return result.rowcount

    def claim_batch(self, worker_id: str, tenant_filter: list[dm.TenantId] | None,
                    limit: int, lease_seconds: int) -> list[dict]:
        now = _utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._db.orm_session() as session:
            stmt = (
                select(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.status == TaskQueueStatus.PENDING.value,
                    om.AcAgentComputeJobModel.run_at <= now,
                    om.AcAgentComputeJobModel.is_deleted.is_(False),
                ).limit(limit)
            )
            if tenant_filter:
                stmt = stmt.where(om.AcAgentComputeJobModel.tenant.in_([str(t) for t in tenant_filter]))
            rows = session.execute(stmt).scalars().all()
            claimed = []
            for row in rows:
                row.status = TaskQueueStatus.RUNNING.value
                row.claimed_by = worker_id
                row.claim_epoch = (row.claim_epoch or 0) + 1
                row.lease_expires_at = lease_until
                row.attempts = (row.attempts or 0) + 1
                claimed.append({
                    "run_id": dm.RunId(row.run_id),
                    "tenant": dm.TenantId(row.tenant),
                    "claim_epoch": row.claim_epoch,
                    "attempts": row.attempts,
                    "cancel_requested": bool(row.cancel_requested),
                    "deadline_at": row.deadline_at,
                })
            return claimed

    def reclaim_batch(self, worker_id: str, limit: int, lease_seconds: int) -> list[dict]:
        """Re-claim RUNNING jobs whose lease expired (crashed pod / lost worker).

        Returns claim dicts extended with `reclaims`; the worker applies the
        D40 circuit breaker: `reclaims` > reclaim_max -> run terminal STUCK.
        """
        now = _utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.status == TaskQueueStatus.RUNNING.value,
                    om.AcAgentComputeJobModel.lease_expires_at < now,
                    om.AcAgentComputeJobModel.is_deleted.is_(False),
                ).limit(limit)
            ).scalars().all()
            reclaimed = []
            for row in rows:
                row.claimed_by = worker_id
                row.claim_epoch = (row.claim_epoch or 0) + 1
                row.lease_expires_at = lease_until
                row.reclaims = (row.reclaims or 0) + 1
                row.attempts = (row.attempts or 0) + 1
                reclaimed.append({
                    "run_id": dm.RunId(row.run_id),
                    "tenant": dm.TenantId(row.tenant),
                    "claim_epoch": row.claim_epoch,
                    "attempts": row.attempts,
                    "reclaims": row.reclaims,
                    "cancel_requested": bool(row.cancel_requested),
                    "deadline_at": row.deadline_at,
                })
            return reclaimed

    def renew_lease(self, run_id: dm.RunId, tenant: dm.TenantId,
                    worker_id: str, lease_seconds: int, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                    om.AcAgentComputeJobModel.is_deleted.is_(False),
                ).values(lease_expires_at=_utcnow() + timedelta(seconds=lease_seconds))
            )
        return result.rowcount > 0

    def reschedule(self, run_id: dm.RunId, tenant: dm.TenantId, worker_id: str,
                   delay_seconds: float, error: str | None, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
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
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                ).values(status=TaskQueueStatus.SUCCEEDED.value, claimed_by=None, lease_expires_at=None)
            )
        return result.rowcount > 0

    def fail(self, run_id: dm.RunId, tenant: dm.TenantId, error: str,
             worker_id: str, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                ).values(status=TaskQueueStatus.FAILED.value, last_error=error, claimed_by=None, lease_expires_at=None)
            )
        return result.rowcount > 0

    def request_cancel(self, run_id: dm.RunId, tenant: dm.TenantId) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.is_deleted.is_(False),
                ).values(cancel_requested=True)
            )
        return result.rowcount > 0

    def count_pending_jobs(self) -> int:
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.status == TaskQueueStatus.PENDING.value,
                    om.AcAgentComputeJobModel.is_deleted.is_(False),
                )
            ).scalar_one()

    def is_cancel_requested(self, run_id: dm.RunId, tenant: dm.TenantId) -> bool:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentComputeJobModel.cancel_requested).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            return bool(row) if row is not None else False