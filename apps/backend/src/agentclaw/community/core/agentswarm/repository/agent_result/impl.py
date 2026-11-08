"""AgentResultRepository — ac_agent_swarm_agent_result persistence
(D19 no-await-in-session). Concept map: Agent 1:1 agent_result — the ONE
final result per agent (uk_result_agent), payload or artifact spill (D31).
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.agent_result import model as om
from agentclaw.community.core.agentswarm.repository.agent_result.protocol import (
    AgentResultRepositoryProtocol,
)
from agentclaw.community.core.agentswarm.repository.types import _dumps, _loads
from agentclaw.community.plugin_api.database import DatabasePlugin


class AgentResultRepository(AgentResultRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, result: dm.AgentResult) -> dm.AgentResult:
        if result.payload is None and result.artifact_key is None:
            raise ValueError("Agent result must carry exactly one of payload or artifact_key (D31)")
        if result.payload is not None and result.artifact_key is not None:
            raise ValueError("Agent result must not carry both payload and artifact_key (D31)")
        try:
            with self._db.orm_session() as session:
                row = om.AcAgentSwarmAgentResultModel(
                    result_id=result.id,
                    run_id=result.run_id,
                    tenant=result.tenant,
                    source_agent_id=result.source_agent_id,
                    payload=_dumps(result.payload) if result.payload is not None else None,
                    artifact_key=result.artifact_key,
                    routed_at=result.routed_at,
                )
                session.add(row)
        except IntegrityError:
            # uk_result_agent: the (run, agent) pair already landed — a racing
            # pod won the double-insert between the existence check and this
            # write. Exactly-once means converge to the surviving row, not
            # surface the collision; a non-pair IntegrityError re-raises.
            existing = self.get_by_run_agent(result.run_id, result.source_agent_id, result.tenant)
            if existing is not None:
                return existing
            raise
        return result

    def get_by_run_agent(self, run_id: dm.RunId, agent_id: dm.AgentId,
                         tenant: dm.TenantId) -> dm.AgentResult | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmAgentResultModel).where(
                    om.AcAgentSwarmAgentResultModel.run_id == run_id,
                    om.AcAgentSwarmAgentResultModel.source_agent_id == agent_id,
                    om.AcAgentSwarmAgentResultModel.tenant == tenant,
                    om.AcAgentSwarmAgentResultModel.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row else None

    def list_by_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> list[dm.AgentResult]:
        with self._db.orm_session() as session:
            # oldest→newest (id asc): the orchestrator prompt keeps the tail
            rows = session.execute(
                select(om.AcAgentSwarmAgentResultModel).where(
                    om.AcAgentSwarmAgentResultModel.run_id == run_id,
                    om.AcAgentSwarmAgentResultModel.tenant == tenant,
                    om.AcAgentSwarmAgentResultModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmAgentResultModel.id)
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentSwarmAgentResultModel).where(
                    om.AcAgentSwarmAgentResultModel.run_id == run_id,
                    om.AcAgentSwarmAgentResultModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentSwarmAgentResultModel) -> dm.AgentResult:
        return dm.AgentResult(
            id=dm.ResultId(row.result_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            source_agent_id=dm.AgentId(row.source_agent_id),
            payload=_loads(row.payload),
            artifact_key=dm.ArtifactKey(row.artifact_key) if row.artifact_key else None,
            routed_at=dm.EpochMillis(row.routed_at or 0),
        )