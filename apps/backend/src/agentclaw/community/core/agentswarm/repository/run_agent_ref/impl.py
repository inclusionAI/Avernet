"""RunAgentRefRepository — ac_agent_swarm_run_agent_ref persistence.

Provenance relates to a run's *catalog entry* (``catalog_name``), one row per
entry, written at submit time. It records which stored definition version an
entry resolved to; the run's own catalog snapshot is untouched.
"""
from __future__ import annotations

from sqlalchemy import delete, select

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.run_agent_ref import (
    model as om,
)
from agentclaw.community.core.agentswarm.repository.run_agent_ref.protocol import (
    RunAgentRefRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


class RunAgentRefRepository(RunAgentRefRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def replace_for_run(self, run_id: dm.RunId, tenant: dm.TenantId,
                        refs: list[dm.RunAgentRef]) -> None:
        """Write one run's provenance, idempotently.

        Deletes this run's existing rows first so a replay converges instead of
        colliding on ``uk_run_catalog``.
        """
        with self._db.orm_session() as session:
            session.execute(
                delete(om.AcAgentSwarmRunAgentRefModel).where(
                    om.AcAgentSwarmRunAgentRefModel.run_id == run_id,
                    om.AcAgentSwarmRunAgentRefModel.tenant == tenant,
                    om.AcAgentSwarmRunAgentRefModel.env == get_current_env(),
                )
            )
            for ref in refs:
                session.add(
                    om.AcAgentSwarmRunAgentRefModel(
                        run_id=ref.run_id,
                        tenant=ref.tenant,
                        env=get_current_env(),
                        catalog_name=ref.catalog_name,
                        definition_id=ref.definition_id,
                        definition_row_id=ref.definition_row_id,
                    )
                )

    def get_for_run(self, run_id: dm.RunId,
                    tenant: dm.TenantId) -> dict[str, dm.RunAgentRef]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmRunAgentRefModel).where(
                    om.AcAgentSwarmRunAgentRefModel.run_id == run_id,
                    om.AcAgentSwarmRunAgentRefModel.tenant == tenant,
                    om.AcAgentSwarmRunAgentRefModel.env == get_current_env(),
                    om.AcAgentSwarmRunAgentRefModel.is_deleted == 0,
                )
            ).scalars().all()
            return {
                r.catalog_name: dm.RunAgentRef(
                    run_id=dm.RunId(r.run_id),
                    tenant=dm.TenantId(r.tenant),
                    catalog_name=r.catalog_name,
                    definition_id=dm.AgentDefinitionId(r.definition_id),
                    definition_row_id=int(r.definition_row_id),
                )
                for r in rows
            }
