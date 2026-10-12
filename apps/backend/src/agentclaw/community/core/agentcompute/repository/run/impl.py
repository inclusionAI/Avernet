"""RunRepository — ac_agent_compute_run persistence (D19 no-await-in-session)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, select, update

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository.run import model as om
from agentclaw.community.core.agentcompute.repository.run.protocol import (
    RunRepositoryProtocol,
)
from agentclaw.community.core.agentcompute.repository.decode import (
    _decode_run_status, _decode_termination_reason,
)
from agentclaw.community.core.agentcompute.repository.job import model as om_job
from agentclaw.community.core.agentcompute.repository.types import (
    _dumps, _loads, _now_ms,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


def _dumps_valid_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_run_status(val: str) -> dm.RunStatus:
    try:
        return dm.RunStatus(val)
    except ValueError:
        return dm.RunStatus.FAILED


def _decode_termination_reason(val: str | None) -> dm.TerminationReason | None:
    if val is None:
        return None
    try:
        return dm.TerminationReason(val)
    except ValueError:
        return dm.TerminationReason.ERROR


class RunRepository(RunRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, run: dm.Run) -> dm.Run:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeRunModel(
                run_id=run.run_id,
                env=run.provider.value,
                tenant=run.tenant,
                goal=run.goal,
                agent_catalog=_dumps([a.__dict__ for a in run.agent_catalog]),
                provider=run.provider,
                request_metadata=_dumps(run.request_metadata.__dict__ if run.request_metadata else None),
                status=run.status.value,
                termination_reason=run.termination_reason.value if run.termination_reason else None,
                root_agent_id=run.root_agent_id,
                final_answer=_dumps(run.final_answer) if run.final_answer is not None else None,
                error=run.error,
                active_idempotency_key=run.active_idempotency_key,
                started_at=run.started_at,
                ended_at=run.ended_at,
            )
            session.add(row)
        return run

    def get(self, run_id: dm.RunId, tenant: dm.TenantId) -> dm.Run | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentComputeRunModel).where(
                    om.AcAgentComputeRunModel.run_id == run_id,
                    om.AcAgentComputeRunModel.tenant == tenant,
                    om.AcAgentComputeRunModel.is_deleted == False,
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row else None

    def get_by_idempotency(self, idempotency_key: str, tenant: dm.TenantId) -> dm.Run | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentComputeRunModel).where(
                    om.AcAgentComputeRunModel.active_idempotency_key == idempotency_key,
                    om.AcAgentComputeRunModel.tenant == tenant,
                    om.AcAgentComputeRunModel.is_deleted == False,
                    om.AcAgentComputeRunModel.status == dm.RunStatus.RUNNING.value,
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row else None

    TERMINAL_STATUSES = {
        dm.RunStatus.COMPLETED, dm.RunStatus.FAILED, dm.RunStatus.CANCELLED,
        dm.RunStatus.TIMED_OUT, dm.RunStatus.STUCK, dm.RunStatus.GUARD_KILLED,
    }

    def update_status(self, run_id: dm.RunId, tenant: dm.TenantId, status: dm.RunStatus,
                      termination_reason: dm.TerminationReason | None = None,
                      final_answer: Any = None, error: str | None = None,
                      claim_epoch: int = 0) -> bool:
        values: dict[str, Any] = {
            "status": status.value,
            "termination_reason": termination_reason.value if termination_reason else None,
            "final_answer": _dumps_valid_json(final_answer),
            "error": error,
        }
        if status in self.TERMINAL_STATUSES:
            values["ended_at"] = _now_ms()
            # The *active* idempotency key only exists to join duplicate
            # submits while the run is live; at terminal it must be cleared
            # so the unique index frees the slot — a retry with the same
            # key then creates a fresh run instead of IntegrityError-ing
            # against the completed row.
            values["active_idempotency_key"] = None
        job_epoch_current = (
            select(om_job.AcAgentComputeJobModel.job_id).where(
                om_job.AcAgentComputeJobModel.run_id == run_id,
                om_job.AcAgentComputeJobModel.tenant == tenant,
                om_job.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                om_job.AcAgentComputeJobModel.is_deleted == False,
            ).exists()
        )
        with self._db.orm_session() as session:
            # D28 fencing: state-mutating writes must carry the caller's claim
            # epoch; a stale worker's write (old epoch) cannot land.
            result = session.execute(
                update(om.AcAgentComputeRunModel).where(
                    om.AcAgentComputeRunModel.run_id == run_id,
                    om.AcAgentComputeRunModel.tenant == tenant,
                    om.AcAgentComputeRunModel.is_deleted == False,
                    job_epoch_current,
                ).values(**values)
            )
        return result.rowcount > 0

    def list_page(self, tenant: dm.TenantId, status: dm.RunStatus | None = None,
                  page: int = 1, page_size: int = 20) -> dm.RunPage:
        offset = max(0, (page - 1)) * page_size
        with self._db.orm_session() as session:
            conditions = [
                om.AcAgentComputeRunModel.tenant == tenant,
                om.AcAgentComputeRunModel.is_deleted == False,
            ]
            if status is not None:
                conditions.append(om.AcAgentComputeRunModel.status == status.value)
            total = session.execute(
                select(func.count()).select_from(om.AcAgentComputeRunModel).where(*conditions)
            ).scalar_one()
            rows = session.execute(
                select(om.AcAgentComputeRunModel).where(*conditions)
                .order_by(om.AcAgentComputeRunModel.id.desc())
                .offset(offset).limit(page_size)
            ).scalars().all()
            return dm.RunPage(
                items=tuple(self._to_domain(r) for r in rows),
                total=total, page=page, page_size=page_size,
            )

    def count_active_runs(self, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(om.AcAgentComputeRunModel).where(
                    om.AcAgentComputeRunModel.tenant == tenant,
                    om.AcAgentComputeRunModel.status == dm.RunStatus.RUNNING.value,
                    om.AcAgentComputeRunModel.is_deleted == False,
                )
            ).scalar_one()

    def count_active_runs_global(self) -> int:
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(om.AcAgentComputeRunModel).where(
                    om.AcAgentComputeRunModel.status == dm.RunStatus.RUNNING.value,
                    om.AcAgentComputeRunModel.is_deleted == False,
                )
            ).scalar_one()

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentComputeRunModel).where(
                    om.AcAgentComputeRunModel.run_id == run_id,
                    om.AcAgentComputeRunModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentComputeRunModel) -> dm.Run:
        catalog_raw = _loads(row.agent_catalog) or []
        # The column stores the spec dicts as plain JSON, so enum fields
        # come back as strings; coerce them back to their typed enums here
        # (the JSON boundary) instead of leaving str values in the typed
        # domain contract, where consumers crash at attribute time.
        agent_catalog = tuple(
            dm.AgentSpec(**{
                **c, "kind": dm.AgentKind(c.get("kind", dm.AgentKind.LLM.value)),
            })
            for c in catalog_raw
        )
        req_md_raw = _loads(row.request_metadata) or {}
        return dm.Run(
            run_id=dm.RunId(row.run_id),
            tenant=dm.TenantId(row.tenant),
            goal=row.goal,
            agent_catalog=agent_catalog,
            provider=dm.ProviderKind.coerce(row.provider),
            request_metadata=dm.RequestMetadata(**req_md_raw),
            status=_decode_run_status(row.status),
            started_at=dm.EpochMillis(row.started_at or 0),
            termination_reason=_decode_termination_reason(row.termination_reason),
            root_agent_id=dm.AgentId(row.root_agent_id) if row.root_agent_id else None,
            final_answer=_loads(row.final_answer),
            error=row.error,
            active_idempotency_key=dm.IdempotencyKey(row.active_idempotency_key) if row.active_idempotency_key else None,
            ended_at=dm.EpochMillis(row.ended_at) if row.ended_at else None,
        )