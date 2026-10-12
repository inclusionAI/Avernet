"""MessageRepository — ac_agent_compute_message persistence (D19 no-await-in-session)."""
from __future__ import annotations


from sqlalchemy import delete, select, update

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository.message import model as om
from agentclaw.community.core.agentcompute.repository.message.protocol import (
    MessageRepositoryProtocol,
)
from agentclaw.community.core.agentcompute.repository.types import _dumps, _loads
from agentclaw.community.plugin_api.database import DatabasePlugin


def _decode_message_role(val: str) -> dm.MessageRole:
    try:
        return dm.MessageRole(val)
    except ValueError:
        return dm.MessageRole.SYSTEM


class MessageRepository(MessageRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, message: dm.Message) -> dm.Message:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeMessageModel(
                message_id=message.id,
                run_id=message.run_id,
                tenant=message.tenant,
                agent_id=message.agent_id,
                seq=message.seq,
                role=message.role.value,
                content=_dumps(message.content),
                decision=_dumps(message.decision.__dict__) if message.decision else None,
                token_usage=_dumps(message.token_usage.__dict__) if message.token_usage else None,
                artifact_key=message.artifact_key,
            )
            session.add(row)
        return message

    def list_by_agent(self, run_id: dm.RunId, agent_id: dm.AgentId, tenant: dm.TenantId) -> list[dm.Message]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeMessageModel).where(
                    om.AcAgentComputeMessageModel.run_id == run_id,
                    om.AcAgentComputeMessageModel.agent_id == agent_id,
                    om.AcAgentComputeMessageModel.tenant == tenant,
                    om.AcAgentComputeMessageModel.is_deleted == False,
                ).order_by(om.AcAgentComputeMessageModel.seq)
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def apply_retention(self, run_id: dm.RunId, agent_id: dm.AgentId,
                        tenant: dm.TenantId, tail: int) -> int:
        with self._db.orm_session() as session:
            total = session.execute(
                select(om.AcAgentComputeMessageModel.seq).where(
                    om.AcAgentComputeMessageModel.run_id == run_id,
                    om.AcAgentComputeMessageModel.agent_id == agent_id,
                    om.AcAgentComputeMessageModel.tenant == tenant,
                    om.AcAgentComputeMessageModel.is_deleted == False,
                ).order_by(om.AcAgentComputeMessageModel.seq.desc())
            ).scalars().all()
            if len(total) <= tail:
                return 0
            cutoff = total[tail]
            result = session.execute(
                update(om.AcAgentComputeMessageModel).where(
                    om.AcAgentComputeMessageModel.run_id == run_id,
                    om.AcAgentComputeMessageModel.agent_id == agent_id,
                    om.AcAgentComputeMessageModel.tenant == tenant,
                    om.AcAgentComputeMessageModel.seq <= cutoff,
                    om.AcAgentComputeMessageModel.is_deleted == False,
                ).values(is_deleted=True)
            )
        return result.rowcount

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentComputeMessageModel).where(
                    om.AcAgentComputeMessageModel.run_id == run_id,
                    om.AcAgentComputeMessageModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentComputeMessageModel) -> dm.Message:
        return dm.Message(
            id=dm.MessageId(row.message_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            agent_id=dm.AgentId(row.agent_id),
            seq=row.seq,
            role=_decode_message_role(row.role),
            content=_loads(row.content),
            created_at=dm.EpochMillis(0),
        )
