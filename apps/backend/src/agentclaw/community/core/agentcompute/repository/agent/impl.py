"""AgentRepository — ac_agent_compute_agent persistence (D19 no-await-in-session)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository.agent import model as om
from agentclaw.community.core.agentcompute.repository.agent.protocol import (
    AgentRepositoryProtocol,
)
from agentclaw.community.core.agentcompute.repository.types import _dumps, _loads, _now_ms
from agentclaw.community.plugin_api.database import DatabasePlugin

AgentStatus = dm.AgentStatus


def _decode_agent_status(val: str) -> dm.AgentStatus:
    try:
        return AgentStatus(val)
    except ValueError:
        return AgentStatus.FAILED


def _decode_bot_state(val: str | None) -> dm.BotState | None:
    if val is None:
        return None
    try:
        return dm.BotState(val)
    except ValueError:
        return None


class AgentRepository(AgentRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, agent: dm.Agent) -> dm.Agent:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeAgentModel(
                agent_id=agent.agent_id,
                run_id=agent.run_id,
                tenant=agent.tenant,
                parent_agent_id=agent.parent_agent_id,
                depth=agent.depth,
                name=agent.name,
                role=agent.role,
                kind=agent.kind.value,
                task_spec=_dumps({
                    "name": agent.task_spec.name,
                    "objective": agent.task_spec.objective,
                    "output_schema": agent.task_spec.output_schema,
                    "boundaries": agent.task_spec.boundaries,
                    "parent_goal": agent.task_spec.parent_goal,
                }),
                spawn_round=agent.spawn_round,
                status=agent.status.value,
                attempts=agent.attempts,
                idempotency_key=agent.idempotency_key,
                output=_dumps(agent.output),
                error=agent.error,
                bot_id=agent.bot_id,
                bot_state=agent.bot_state.value if agent.bot_state else None,
                started_at=agent.started_at,
                ended_at=agent.ended_at,
            )
            session.add(row)
        return agent

    def get(self, run_id: dm.RunId, agent_id: dm.AgentId, tenant: dm.TenantId) -> dm.Agent | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentComputeAgentModel).where(
                    om.AcAgentComputeAgentModel.run_id == run_id,
                    om.AcAgentComputeAgentModel.agent_id == agent_id,
                    om.AcAgentComputeAgentModel.tenant == tenant,
                    om.AcAgentComputeAgentModel.is_deleted == False,
                )
            ).scalar_one_or_none()
            return self._to_domain(row, tenant) if row else None

    def list_by_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> list[dm.Agent]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeAgentModel).where(
                    om.AcAgentComputeAgentModel.run_id == run_id,
                    om.AcAgentComputeAgentModel.tenant == tenant,
                    om.AcAgentComputeAgentModel.is_deleted == False,
                ).order_by(om.AcAgentComputeAgentModel.id)
            ).scalars().all()
            return [self._to_domain(r, tenant) for r in rows]

    def list_by_run_with_rows(self, run_id: dm.RunId,
                              tenant: dm.TenantId) -> list[tuple[int, dm.Agent]]:
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeAgentModel.id,
                       om.AcAgentComputeAgentModel).where(
                    om.AcAgentComputeAgentModel.run_id == run_id,
                    om.AcAgentComputeAgentModel.tenant == tenant,
                    om.AcAgentComputeAgentModel.is_deleted == False,
                ).order_by(om.AcAgentComputeAgentModel.id)
            ).all()
            return [(r[0], self._to_domain(r[1], tenant)) for r in rows]

    def update_status(self, run_id: dm.RunId, agent_id: dm.AgentId, tenant: dm.TenantId,
                      status: str, attempts: int | None = None,
                      output: dict | None = None, error: str | None = None,
                      bot_id: str | None = None, bot_state: str | None = None,
                      claim_epoch: int = 0) -> bool:
        values: dict[str, Any] = {"status": status}
        if attempts is not None: values["attempts"] = attempts
        if error is not None: values["error"] = error
        if output is not None: values["output"] = _dumps(output)
        if bot_id is not None: values["bot_id"] = bot_id
        if bot_state is not None: values["bot_state"] = bot_state
        values["ended_at"] = _now_ms() if status in ("COMPLETED", "FAILED", "KILLED") else None
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeAgentModel).where(
                    om.AcAgentComputeAgentModel.run_id == run_id,
                    om.AcAgentComputeAgentModel.agent_id == agent_id,
                    om.AcAgentComputeAgentModel.tenant == tenant,
                    om.AcAgentComputeAgentModel.is_deleted == False,
                ).values(**values)
            )
        return result.rowcount > 0

    def purge_run(self, run_id: dm.RunId, tenant: dm.TenantId) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                delete(om.AcAgentComputeAgentModel).where(
                    om.AcAgentComputeAgentModel.run_id == run_id,
                    om.AcAgentComputeAgentModel.tenant == tenant,
                )
            )
        return result.rowcount

    @staticmethod
    def _to_domain(row: om.AcAgentComputeAgentModel, tenant: dm.TenantId) -> dm.Agent:
        ts_raw = _loads(row.task_spec) or {}
        return dm.Agent(
            agent_id=dm.AgentId(row.agent_id),
            run_id=dm.RunId(row.run_id),
            tenant=row.tenant,
            parent_agent_id=dm.AgentId(row.parent_agent_id) if row.parent_agent_id else None,
            depth=row.depth,
            name=row.name,
            role=row.role or "",
            kind=dm.AgentKind(row.kind),
            task_spec=dm.TaskEnvelope(**ts_raw),
            spawn_round=row.spawn_round,
            status=_decode_agent_status(row.status),
            attempts=row.attempts,
            idempotency_key=dm.IdempotencyKey(row.idempotency_key),
            output=_loads(row.output) or {},
            error=row.error,
            bot_id=dm.BotId(row.bot_id) if row.bot_id else None,
            bot_state=_decode_bot_state(row.bot_state),
            started_at=dm.EpochMillis(row.started_at) if row.started_at else None,
            ended_at=dm.EpochMillis(row.ended_at) if row.ended_at else None,
        )