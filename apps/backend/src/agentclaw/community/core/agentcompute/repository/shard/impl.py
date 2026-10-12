"""ResultShardRepository — ac_agent_compute_result_shard persistence (D19 no-await-in-session)."""
from __future__ import annotations

from sqlalchemy import delete, select

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository.shard import model as om
from agentclaw.community.core.agentcompute.repository.shard.protocol import (
    ResultShardRepositoryProtocol,
)
from agentclaw.community.core.agentcompute.repository.types import _dumps, _loads
from agentclaw.community.plugin_api.database import DatabasePlugin


class ResultShardRepository(ResultShardRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, shard: dm.ResultShard) -> dm.ResultShard:
        if shard.payload is None and shard.artifact_key is None:
            raise ValueError("Result shard must carry exactly one of payload or artifact_key (D31)")
        if shard.payload is not None and shard.artifact_key is not None:
            raise ValueError("Result shard must not carry both payload and artifact_key (D31)")
        with self._db.orm_session() as session:
            row = om.AcAgentComputeResultShardModel(
                shard_id=shard.id,
                run_id=shard.run_id,
                tenant=shard.tenant,
                source_agent_id=shard.source_agent_id,
                payload=_dumps(shard.payload) if shard.payload is not None else None,
                artifact_key=shard.artifact_key,
                routed_at=shard.routed_at,
            )
            session.add(row)
        return shard

    def get_by_run_agent(self, run_id: dm.RunId, agent_id: dm.AgentId,
                         tenant: dm.TenantId) -> dm.ResultShard | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentComputeResultShardModel).where(
                    om.AcAgentComputeResultShardModel.run_id == run_id,
                    om.AcAgentComputeResultShardModel.source_agent_id == agent_id,
                    om.AcAgentComputeResultShardModel.tenant == tenant,
                    om.AcAgentComputeResultShardModel.is_deleted == False,
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row else None

    def list_by_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> list[dm.ResultShard]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeResultShardModel).where(
                    om.AcAgentComputeResultShardModel.run_id == run_id,
                    om.AcAgentComputeResultShardModel.tenant == tenant,
                    om.AcAgentComputeResultShardModel.is_deleted == False,
                )
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentComputeResultShardModel).where(
                    om.AcAgentComputeResultShardModel.run_id == run_id,
                    om.AcAgentComputeResultShardModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentComputeResultShardModel) -> dm.ResultShard:
        return dm.ResultShard(
            id=dm.ShardId(row.shard_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            source_agent_id=dm.AgentId(row.source_agent_id),
            payload=_loads(row.payload),
            artifact_key=dm.ArtifactKey(row.artifact_key) if row.artifact_key else None,
            routed_at=dm.EpochMillis(row.routed_at or 0),
        )