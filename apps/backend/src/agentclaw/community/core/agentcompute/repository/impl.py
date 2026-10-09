"""Unified agentcompute repositories (DatabasePlugin.orm_session, D19 no-await-in-session)."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.repository import models as om
from agentclaw.community.core.agentcompute.repository.protocols import (
    AgentRepositoryProtocol,
    GuardEventRepositoryProtocol,
    JobRepositoryProtocol,
    MessageRepositoryProtocol,
    ResultShardRepositoryProtocol,
    RoundRepositoryProtocol,
    RunRepositoryProtocol,
)
from agentclaw.community.core.agentcompute.repository.types import (
    _dumps, _loads, _now_ms,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

AgentStatus = dm.AgentStatus
TaskQueueStatus = dm.TaskQueueStatus


def _new_id() -> str:
    return uuid.uuid4().hex


def _dumps_valid_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_agent_status(val: str) -> dm.AgentStatus:
    try:
        return AgentStatus(val)
    except ValueError:
        return AgentStatus.FAILED


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


def _decode_bot_state(val: str | None) -> dm.BotState | None:
    if val is None:
        return None
    try:
        return dm.BotState(val)
    except ValueError:
        return None


def _decode_finish_reason(val: str | None) -> dm.FinishReason | None:
    if val is None:
        return None
    try:
        return dm.FinishReason(val)
    except ValueError:
        return dm.FinishReason.ERROR


def _decode_guard_kind(val: str) -> dm.GuardKind:
    try:
        return dm.GuardKind(val)
    except ValueError:
        return dm.GuardKind.KILL


def _decode_message_role(val: str) -> dm.MessageRole:
    try:
        return dm.MessageRole(val)
    except ValueError:
        return dm.MessageRole.SYSTEM


class RunRepository(RunRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, run: dm.Run) -> dm.Run:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeRunModel(
                run_id=run.run_id,
                env=run.provider or "default",
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
        job_epoch_current = (
            select(om.AcAgentComputeJobModel.job_id).where(
                om.AcAgentComputeJobModel.run_id == run_id,
                om.AcAgentComputeJobModel.tenant == tenant,
                om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                om.AcAgentComputeJobModel.is_deleted == False,
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

    @staticmethod
    def _to_domain(row: om.AcAgentComputeRunModel) -> dm.Run:
        catalog_raw = _loads(row.agent_catalog) or []
        agent_catalog = tuple(dm.AgentSpecRaw(**c) for c in catalog_raw)
        req_md_raw = _loads(row.request_metadata) or {}
        return dm.Run(
            run_id=dm.RunId(row.run_id),
            tenant=dm.TenantId(row.tenant),
            goal=row.goal,
            agent_catalog=agent_catalog,
            provider=dm.ProviderId(row.provider or "default"),
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
                    om.AcAgentComputeRoundModel.is_deleted == False,
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
                    om.AcAgentComputeRoundModel.is_deleted == False,
                ).order_by(om.AcAgentComputeRoundModel.id)
            ).all()
            return [(r[0], self._to_domain(r[1])) for r in rows]

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


class JobRepository(JobRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def enqueue(self, run_id: dm.RunId, tenant: dm.TenantId,
                deadline_seconds: int, run_at: datetime | None = None) -> om.AcAgentComputeJobModel:
        with self._db.orm_session() as session:
            row = om.AcAgentComputeJobModel(
                job_id=_new_id(),
                run_id=run_id,
                status=TaskQueueStatus.PENDING.value,
                run_at=run_at or datetime.utcnow(),
                deadline_at=datetime.utcnow() + timedelta(seconds=deadline_seconds),
                tenant=tenant,
            )
            session.add(row)
        return row

    def claim_batch(self, worker_id: str, tenant_filter: list[dm.TenantId] | None,
                    limit: int, lease_seconds: int) -> list[dict]:
        now = datetime.utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._db.orm_session() as session:
            stmt = (
                select(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.status == TaskQueueStatus.PENDING.value,
                    om.AcAgentComputeJobModel.run_at <= now,
                    om.AcAgentComputeJobModel.is_deleted == False,
                ).limit(limit)
            )
            if tenant_filter:
                stmt = stmt.where(om.AcAgentComputeJobModel.tenant.in_([str(t) for t in tenant_filter]))
            rows = session.execute(stmt).scalars().all()
            claimed = []
            for row in rows:
                row.status = TaskQueueStatus.RUNNING.value
                row.claimed_by = worker_id
                row.claim_epoch = (row.claim_epoch or 0) + 1
                row.lease_expires_at = lease_until
                row.attempts = (row.attempts or 0) + 1
                claimed.append({
                    "run_id": dm.RunId(row.run_id),
                    "tenant": dm.TenantId(row.tenant),
                    "claim_epoch": row.claim_epoch,
                    "attempts": row.attempts,
                    "cancel_requested": bool(row.cancel_requested),
                    "deadline_at": row.deadline_at,
                })
            return claimed

    def reclaim_batch(self, worker_id: str, limit: int, lease_seconds: int) -> list[dict]:
        """Re-claim RUNNING jobs whose lease expired (crashed pod / lost worker).

        Returns claim dicts extended with `reclaims`; the worker applies the
        D40 circuit breaker: `reclaims` > reclaim_max -> run terminal STUCK.
        """
        now = datetime.utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.status == TaskQueueStatus.RUNNING.value,
                    om.AcAgentComputeJobModel.lease_expires_at < now,
                    om.AcAgentComputeJobModel.is_deleted == False,
                ).limit(limit)
            ).scalars().all()
            reclaimed = []
            for row in rows:
                row.claimed_by = worker_id
                row.claim_epoch = (row.claim_epoch or 0) + 1
                row.lease_expires_at = lease_until
                row.reclaims = (row.reclaims or 0) + 1
                row.attempts = (row.attempts or 0) + 1
                reclaimed.append({
                    "run_id": dm.RunId(row.run_id),
                    "tenant": dm.TenantId(row.tenant),
                    "claim_epoch": row.claim_epoch,
                    "attempts": row.attempts,
                    "reclaims": row.reclaims,
                    "cancel_requested": bool(row.cancel_requested),
                    "deadline_at": row.deadline_at,
                })
            return reclaimed

    def renew_lease(self, run_id: dm.RunId, tenant: dm.TenantId,
                    worker_id: str, lease_seconds: int, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                    om.AcAgentComputeJobModel.is_deleted == False,
                ).values(lease_expires_at=datetime.utcnow() + timedelta(seconds=lease_seconds))
            )
        return result.rowcount > 0

    def reschedule(self, run_id: dm.RunId, tenant: dm.TenantId, worker_id: str,
                   delay_seconds: float, error: str | None, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                ).values(
                    status=TaskQueueStatus.PENDING.value,
                    run_at=datetime.utcnow() + timedelta(seconds=delay_seconds),
                    last_error=error,
                    claimed_by=None,
                    lease_expires_at=None,
                )
            )
        return result.rowcount > 0

    def complete(self, run_id: dm.RunId, tenant: dm.TenantId,
                 worker_id: str, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                ).values(status=TaskQueueStatus.SUCCEEDED.value, claimed_by=None, lease_expires_at=None)
            )
        return result.rowcount > 0

    def fail(self, run_id: dm.RunId, tenant: dm.TenantId, error: str,
             worker_id: str, claim_epoch: int) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.claimed_by == worker_id,
                    om.AcAgentComputeJobModel.claim_epoch == claim_epoch,
                ).values(status=TaskQueueStatus.FAILED.value, last_error=error, claimed_by=None, lease_expires_at=None)
            )
        return result.rowcount > 0

    def request_cancel(self, run_id: dm.RunId, tenant: dm.TenantId) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.is_deleted == False,
                ).values(cancel_requested=True)
            )
        return result.rowcount > 0

    def count_pending_jobs(self) -> int:
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(om.AcAgentComputeJobModel).where(
                    om.AcAgentComputeJobModel.status == TaskQueueStatus.PENDING.value,
                    om.AcAgentComputeJobModel.is_deleted == False,
                )
            ).scalar_one()

    def is_cancel_requested(self, run_id: dm.RunId, tenant: dm.TenantId) -> bool:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentComputeJobModel.cancel_requested).where(
                    om.AcAgentComputeJobModel.run_id == run_id,
                    om.AcAgentComputeJobModel.tenant == tenant,
                    om.AcAgentComputeJobModel.is_deleted == False,
                )
            ).scalar_one_or_none()
            return bool(row) if row is not None else False