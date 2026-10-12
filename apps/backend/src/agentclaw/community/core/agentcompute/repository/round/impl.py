"""RoundRepository — ac_agent_compute_round persistence (D19 no-await-in-session)."""
from __future__ import annotations

from sqlalchemy import delete, select

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository.round import model as om
from agentclaw.community.core.agentcompute.repository.round.protocol import (
    RoundRepositoryProtocol,
)
from agentclaw.community.core.agentcompute.repository.types import _dumps, _loads
from agentclaw.community.plugin_api.database import DatabasePlugin


class RoundRepository(RoundRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, round_record: dm.Round) -> dm.Round:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeRoundModel(
                round_id=round_record.id,
                run_id=round_record.run_id,
                tenant=round_record.tenant,
                stage_index=round_record.stage_index,
                orchestrator_steps=round_record.orchestrator_steps,
                max_subagent_steps=round_record.max_subagent_steps,
                cohort_agent_ids=_dumps(list(round_record.cohort_agent_ids)),
                started_at=round_record.started_at,
                ended_at=round_record.ended_at,
            )
            session.add(row)
        return round_record

    def list_by_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> list[dm.Round]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeRoundModel).where(
                    om.AcAgentComputeRoundModel.run_id == run_id,
                    om.AcAgentComputeRoundModel.tenant == tenant,
                    om.AcAgentComputeRoundModel.is_deleted.is_(False),
                ).order_by(om.AcAgentComputeRoundModel.stage_index)
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def list_by_run_with_rows(self, run_id: dm.RunId,
                              tenant: dm.TenantId) -> list[tuple[int, dm.Round]]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeRoundModel.id,
                       om.AcAgentComputeRoundModel).where(
                    om.AcAgentComputeRoundModel.run_id == run_id,
                    om.AcAgentComputeRoundModel.tenant == tenant,
                    om.AcAgentComputeRoundModel.is_deleted.is_(False),
                ).order_by(om.AcAgentComputeRoundModel.id)
            ).all()
            return [(r[0], self._to_domain(r[1])) for r in rows]

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentComputeRoundModel).where(
                    om.AcAgentComputeRoundModel.run_id == run_id,
                    om.AcAgentComputeRoundModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentComputeRoundModel) -> dm.Round:
        return dm.Round(
            id=dm.RoundId(row.round_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            stage_index=row.stage_index,
            orchestrator_steps=row.orchestrator_steps,
            max_subagent_steps=row.max_subagent_steps,
            cohort_agent_ids=tuple(_loads(row.cohort_agent_ids) or []),
            started_at=dm.EpochMillis(row.started_at or 0),
            ended_at=dm.EpochMillis(row.ended_at) if row.ended_at else None,
        )