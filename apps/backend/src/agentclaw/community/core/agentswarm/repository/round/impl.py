"""RoundRepository — ac_agent_swarm_round persistence (D19 no-await-in-session)."""
from __future__ import annotations

from sqlalchemy import delete, select, update

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.round import model as om
from agentclaw.community.core.agentswarm.repository.round.protocol import (
    RoundRepositoryProtocol,
)
from agentclaw.community.core.agentswarm.repository.types import _dumps, _loads, _now_ms
from agentclaw.community.plugin_api.database import DatabasePlugin


class RoundRepository(RoundRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, round_record: dm.Round) -> dm.Round:
        with self._db.orm_session() as session:
            row = om.AcAgentSwarmRoundModel(
                round_id=round_record.id,
                run_id=round_record.run_id,
                tenant=round_record.tenant,
                stage_index=round_record.stage_index,
                orchestrator_steps=round_record.orchestrator_steps,
                slowest_attempt_steps=round_record.slowest_attempt_steps,
                cohort_agent_ids=_dumps(list(round_record.cohort_agent_ids)),
                started_at=round_record.started_at,
                ended_at=round_record.ended_at,
            )
            session.add(row)
        return round_record

    def close_open(self, run_id: dm.RunId, tenant: dm.TenantId,
                   ended_at: int | None = None) -> int:
        """Close every round of this run that is still open.

        A round row is written once, when its stage starts, and nothing closes
        it — so ``ended_at`` stayed NULL on every run, completed ones included,
        and a reader could not tell a finished stage from a running one. The
        run's own termination is the point where no stage can still be
        executing, so that is where the remainder is closed.

        Guarded by ``ended_at IS NULL`` so calling it twice (or after a stage
        already closed itself) is a no-op rather than a rewrite.
        """
        stamp = ended_at if ended_at is not None else _now_ms()
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmRoundModel)
                .where(
                    om.AcAgentSwarmRoundModel.run_id == run_id,
                    om.AcAgentSwarmRoundModel.tenant == tenant,
                    om.AcAgentSwarmRoundModel.is_deleted.is_(False),
                    om.AcAgentSwarmRoundModel.ended_at.is_(None),
                )
                .values(ended_at=stamp)
            )
            return int(result.rowcount or 0)

    def list_by_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> list[dm.Round]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmRoundModel).where(
                    om.AcAgentSwarmRoundModel.run_id == run_id,
                    om.AcAgentSwarmRoundModel.tenant == tenant,
                    om.AcAgentSwarmRoundModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmRoundModel.stage_index)
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def list_by_run_with_rows(self, run_id: dm.RunId,
                              tenant: dm.TenantId) -> list[tuple[int, dm.Round]]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmRoundModel.id,
                       om.AcAgentSwarmRoundModel).where(
                    om.AcAgentSwarmRoundModel.run_id == run_id,
                    om.AcAgentSwarmRoundModel.tenant == tenant,
                    om.AcAgentSwarmRoundModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmRoundModel.id)
            ).all()
            return [(r[0], self._to_domain(r[1])) for r in rows]

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentSwarmRoundModel).where(
                    om.AcAgentSwarmRoundModel.run_id == run_id,
                    om.AcAgentSwarmRoundModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentSwarmRoundModel) -> dm.Round:
        return dm.Round(
            id=dm.RoundId(row.round_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            stage_index=row.stage_index,
            orchestrator_steps=row.orchestrator_steps,
            slowest_attempt_steps=row.slowest_attempt_steps,
            cohort_agent_ids=tuple(_loads(row.cohort_agent_ids) or []),
            started_at=dm.EpochMillis(row.started_at or 0),
            ended_at=dm.EpochMillis(row.ended_at) if row.ended_at else None,
        )