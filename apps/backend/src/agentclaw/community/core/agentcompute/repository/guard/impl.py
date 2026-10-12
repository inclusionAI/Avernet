"""GuardEventRepository — ac_agent_compute_guard_event persistence (D19 no-await-in-session)."""
from __future__ import annotations

from sqlalchemy import delete, select

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository.guard import model as om
from agentclaw.community.core.agentcompute.repository.guard.protocol import (
    GuardEventRepositoryProtocol,
)
from agentclaw.community.core.agentcompute.repository.types import _dumps, _loads
from agentclaw.community.plugin_api.database import DatabasePlugin


def _decode_guard_kind(val: str) -> dm.GuardKind:
    try:
        return dm.GuardKind(val)
    except ValueError:
        return dm.GuardKind.KILL


class GuardEventRepository(GuardEventRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, event: dm.GuardEvent) -> dm.GuardEvent:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeGuardEventModel(
                event_id=event.id,
                run_id=event.run_id,
                tenant=event.tenant,
                agent_id=event.agent_id,
                kind=event.kind.value,
                details=_dumps(event.details),
            )
            session.add(row)
        return event

    def list_by_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> list[dm.GuardEvent]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeGuardEventModel).where(
                    om.AcAgentComputeGuardEventModel.run_id == run_id,
                    om.AcAgentComputeGuardEventModel.tenant == tenant,
                    om.AcAgentComputeGuardEventModel.is_deleted == False,
                ).order_by(om.AcAgentComputeGuardEventModel.id)
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def list_by_run_with_rows(self, run_id: dm.RunId,
                              tenant: dm.TenantId) -> list[tuple[int, dm.GuardEvent]]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeGuardEventModel.id,
                       om.AcAgentComputeGuardEventModel).where(
                    om.AcAgentComputeGuardEventModel.run_id == run_id,
                    om.AcAgentComputeGuardEventModel.tenant == tenant,
                    om.AcAgentComputeGuardEventModel.is_deleted == False,
                ).order_by(om.AcAgentComputeGuardEventModel.id)
            ).all()
            return [(r[0], self._to_domain(r[1])) for r in rows]

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentComputeGuardEventModel).where(
                    om.AcAgentComputeGuardEventModel.run_id == run_id,
                    om.AcAgentComputeGuardEventModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentComputeGuardEventModel) -> dm.GuardEvent:
        return dm.GuardEvent(
            id=dm.EventId(row.event_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            agent_id=dm.AgentId(row.agent_id) if row.agent_id else None,
            kind=_decode_guard_kind(row.kind),
            details=_loads(row.details) or {},
            created_at=dm.EpochMillis(0),
        )