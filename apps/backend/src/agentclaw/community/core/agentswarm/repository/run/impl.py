"""RunRepository — ac_agent_swarm_run persistence (D19 no-await-in-session)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.run import model as om
from agentclaw.community.core.agentswarm.repository.run.protocol import (
    RunRepositoryProtocol,
)
from agentclaw.community.core.agentswarm.repository.decode import (
    _decode_run_status, _decode_termination_reason,
)
from agentclaw.community.core.agentswarm.repository.job import model as om_job
from agentclaw.community.core.agentswarm.repository.types import (
    _dumps, _loads, _now_ms,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


def _dumps_valid_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


class RunRepository(RunRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, run: dm.Run) -> dm.Run:
        try:
            with self._db.orm_session() as session:
                row = om.AcAgentSwarmRunModel(
                    run_id=run.run_id,
                    env=get_current_env(),
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
                    owner_id=run.owner_id,
                    model=getattr(run.run_options, 'model_name', None) or '',
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                )
                session.add(row)
        except IntegrityError:
            # uk_run_idem: the (env, tenant, active_idempotency_key) slot
            # already landed — a duplicate submit lost the race inside the
            # get_by_idempotency → create TOCTOU window in submit_async.
            # Idempotency means converge to the surviving run, not surface
            # the collision; the submit plane skips its enqueue by comparing
            # the returned run_id. Only this key collision converges: with
            # no active key (e.g. a uk_run_id PK collision) there is nothing
            # sound to join, and the IntegrityError re-raises.
            if run.active_idempotency_key is None:
                raise
            existing = self.get_by_idempotency(
                run.active_idempotency_key, run.tenant,
            )
            if existing is not None:
                return existing
            raise
        return run

    def get(self, run_id: dm.RunId, tenant: dm.TenantId) -> dm.Run | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.run_id == run_id,
                    om.AcAgentSwarmRunModel.tenant == tenant,
                    om.AcAgentSwarmRunModel.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row else None

    def get_by_idempotency(self, idempotency_key: str, tenant: dm.TenantId) -> dm.Run | None:
        # uk_run_idem is (env, tenant, active_idempotency_key): in a
        # multi-env single-DB deployment the join must stay inside the
        # current env or the create() convergence path can return
        # another env's run.
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.active_idempotency_key == idempotency_key,
                    om.AcAgentSwarmRunModel.tenant == tenant,
                    om.AcAgentSwarmRunModel.is_deleted.is_(False),
                    om.AcAgentSwarmRunModel.status == dm.RunStatus.RUNNING.value,
                    om.AcAgentSwarmRunModel.env == get_current_env(),
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
            select(om_job.AcAgentSwarmJobModel.job_id).where(
                om_job.AcAgentSwarmJobModel.run_id == run_id,
                om_job.AcAgentSwarmJobModel.tenant == tenant,
                om_job.AcAgentSwarmJobModel.claim_epoch == claim_epoch,
                om_job.AcAgentSwarmJobModel.is_deleted.is_(False),
            ).exists()
        )
        with self._db.orm_session() as session:
            # D28 fencing: state-mutating writes must carry the caller's claim
            # epoch; a stale worker's write (old epoch) cannot land.
            result = session.execute(
                update(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.run_id == run_id,
                    om.AcAgentSwarmRunModel.tenant == tenant,
                    om.AcAgentSwarmRunModel.is_deleted.is_(False),
                    job_epoch_current,
                ).values(**values)
            )
        return result.rowcount > 0

    def fail_unclaimed(self, run_id: dm.RunId, tenant: dm.TenantId,
                       error: str) -> bool:
        """Submit-plane compensation for a failed enqueue.

        Deliberately NOT behind the D28 job-epoch fence: that fence
        guards worker-owned writes against stale claimers, but this
        method exists precisely for the case where `enqueue` raised
        BEFORE the job row committed — no job row exists at any epoch,
        so a fenced UPDATE would match zero rows and strand the run
        RUNNING forever (holding the tenant admission slot and the
        active idempotency key). The caller is the submit plane
        reclaiming a run it just created and never handed to a worker;
        the status==RUNNING predicate alone prevents clobbering a
        racing terminal write.
        """
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.run_id == run_id,
                    om.AcAgentSwarmRunModel.tenant == tenant,
                    om.AcAgentSwarmRunModel.is_deleted.is_(False),
                    om.AcAgentSwarmRunModel.status
                    == dm.RunStatus.RUNNING.value,
                ).values(
                    status=dm.RunStatus.FAILED.value,
                    error=error,
                    termination_reason=None,
                    active_idempotency_key=None,
                    ended_at=_now_ms(),
                )
            )
        return result.rowcount == 1

    def list_page(self, tenant: dm.TenantId, status: dm.RunStatus | None = None,
                  page: int = 1, page_size: int = 20) -> dm.RunPage:
        offset = max(0, (page - 1)) * page_size
        with self._db.orm_session() as session:
            conditions = [
                om.AcAgentSwarmRunModel.tenant == tenant,
                om.AcAgentSwarmRunModel.is_deleted.is_(False),
            ]
            if status is not None:
                conditions.append(om.AcAgentSwarmRunModel.status == status.value)
            total = session.execute(
                select(func.count()).select_from(om.AcAgentSwarmRunModel).where(*conditions)
            ).scalar_one()
            rows = session.execute(
                select(om.AcAgentSwarmRunModel).where(*conditions)
                .order_by(om.AcAgentSwarmRunModel.id.desc())
                .offset(offset).limit(page_size)
            ).scalars().all()
            return dm.RunPage(
                items=tuple(self._to_domain(r) for r in rows),
                total=total, page=page, page_size=page_size,
            )

    def list_status_page(self, status: dm.RunStatus, page: int = 1,
                         page_size: int = 100) -> dm.RunPage:
        """Fleet-wide status page for the maintenance reaper (no tenant).

        Mirrors list_page's shape but always status-filtered and env-bound:
        a multi-env single-DB deployment must not cross-sweep another
        env's runs — the job-repo claim surfaces the reaper pairs with
        bind the same way.
        """
        offset = max(0, (page - 1)) * page_size
        with self._db.orm_session() as session:
            conditions = [
                om.AcAgentSwarmRunModel.env == get_current_env(),
                om.AcAgentSwarmRunModel.status == status.value,
                om.AcAgentSwarmRunModel.is_deleted.is_(False),
            ]
            total = session.execute(
                select(func.count()).select_from(om.AcAgentSwarmRunModel).where(*conditions)
            ).scalar_one()
            rows = session.execute(
                select(om.AcAgentSwarmRunModel).where(*conditions)
                .order_by(om.AcAgentSwarmRunModel.id.desc())
                .offset(offset).limit(page_size)
            ).scalars().all()
            return dm.RunPage(
                items=tuple(self._to_domain(r) for r in rows),
                total=total, page=page, page_size=page_size,
            )

    def count_active_runs(self, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.tenant == tenant,
                    om.AcAgentSwarmRunModel.status == dm.RunStatus.RUNNING.value,
                    om.AcAgentSwarmRunModel.is_deleted.is_(False),
                )
            ).scalar_one()

    def count_active_runs_global(self) -> int:
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.status == dm.RunStatus.RUNNING.value,
                    om.AcAgentSwarmRunModel.is_deleted.is_(False),
                )
            ).scalar_one()

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.run_id == run_id,
                    om.AcAgentSwarmRunModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentSwarmRunModel) -> dm.Run:
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
            owner_id=row.owner_id or "",
            run_options=dm.RunOptions.from_metadata(row.request_metadata) if row.request_metadata else None,
            termination_reason=_decode_termination_reason(row.termination_reason),
            root_agent_id=dm.AgentId(row.root_agent_id) if row.root_agent_id else None,
            final_answer=_loads(row.final_answer),
            error=row.error,
            active_idempotency_key=dm.IdempotencyKey(row.active_idempotency_key) if row.active_idempotency_key else None,
            ended_at=dm.EpochMillis(row.ended_at) if row.ended_at else None,
        )