"""Transactional repository for Bot execution identity bindings."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.execution_identity.contracts import (
    ExecutionIdentityBinding,
    ExecutionIdentityChangeInProgressError,
    ExecutionIdentityNotFoundError,
    ExecutionIdentityStatus,
    ExecutionIdentityType,
)
from agentclaw.community.core.execution_identity.models import (
    BotExecutionIdentityBindingModel,
)
from agentclaw.community.core.repository.protocols.identity import (
    ExecutionIdentityRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env


def _record(row: BotExecutionIdentityBindingModel) -> ExecutionIdentityBinding:
    return ExecutionIdentityBinding(
        id=int(row.id),
        bot_pk=int(row.bot_pk),
        execution_workno=str(row.execution_workno),
        identity_type=ExecutionIdentityType(str(row.identity_type)),
        status=ExecutionIdentityStatus(str(row.status)),
        authorization_id=row.authorization_id,
        credential_id=row.credential_id,
        agent_id=row.agent_id,
        credential_status=row.credential_status,
    )


class ExecutionIdentityRepository(ExecutionIdentityRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    @staticmethod
    def _state_query(session, *, bot_pk: int, status: ExecutionIdentityStatus):
        return (
            session.query(BotExecutionIdentityBindingModel)
            .filter(
                BotExecutionIdentityBindingModel.bot_pk == bot_pk,
                BotExecutionIdentityBindingModel.env == get_current_env(),
                BotExecutionIdentityBindingModel.status == status.value,
            )
            .order_by(BotExecutionIdentityBindingModel.id.desc())
        )

    def get_active(self, *, bot_pk: int) -> ExecutionIdentityBinding | None:
        with self._db.orm_session() as session:
            row = self._state_query(
                session, bot_pk=bot_pk, status=ExecutionIdentityStatus.ACTIVE
            ).first()
            return _record(row) if row is not None else None

    def get_pending(self, *, bot_pk: int) -> ExecutionIdentityBinding | None:
        with self._db.orm_session() as session:
            row = self._state_query(
                session, bot_pk=bot_pk, status=ExecutionIdentityStatus.PENDING
            ).first()
            return _record(row) if row is not None else None

    def begin_pending(
        self,
        *,
        bot_pk: int,
        execution_workno: str,
        identity_type: ExecutionIdentityType,
        modifier_id: str,
    ) -> ExecutionIdentityBinding:
        env = get_current_env()
        with self._db.transactional_orm_session() as session:
            # The Bot row is the aggregate lock: only one PENDING transition is
            # admitted for a Bot, even when two operators race.
            session.query(BotModel).filter(
                BotModel.id == bot_pk, BotModel.env == env
            ).with_for_update().one()
            pending = self._state_query(
                session, bot_pk=bot_pk, status=ExecutionIdentityStatus.PENDING
            ).with_for_update().first()
            if pending is not None:
                raise ExecutionIdentityChangeInProgressError(
                    f"Bot {bot_pk} already has a pending execution identity change"
                )
            row = BotExecutionIdentityBindingModel(
                bot_pk=bot_pk,
                execution_workno=execution_workno,
                identity_type=identity_type.value,
                status=ExecutionIdentityStatus.PENDING.value,
                modifier_id=modifier_id,
                env=env,
            )
            session.add(row)
            session.flush()
            result = _record(row)
        return result

    def record_credential_result(
        self,
        *,
        binding_id: int,
        authorization_id: str | None,
        credential_id: str | None,
        agent_id: str | None,
        credential_status: str | None,
        modifier_id: str,
    ) -> ExecutionIdentityBinding:
        with self._db.transactional_orm_session() as session:
            row = session.query(BotExecutionIdentityBindingModel).filter(
                BotExecutionIdentityBindingModel.id == binding_id,
                BotExecutionIdentityBindingModel.status
                == ExecutionIdentityStatus.PENDING.value,
            ).with_for_update().one_or_none()
            if row is None:
                raise ExecutionIdentityNotFoundError("Pending binding not found")
            row.authorization_id = authorization_id
            row.credential_id = credential_id
            row.agent_id = agent_id
            row.credential_status = credential_status
            row.modifier_id = modifier_id
            session.flush()
            result = _record(row)
        return result

    def activate_pending(
        self, *, binding_id: int, modifier_id: str
    ) -> ExecutionIdentityBinding:
        env = get_current_env()
        with self._db.transactional_orm_session() as session:
            pending = session.query(BotExecutionIdentityBindingModel).filter(
                BotExecutionIdentityBindingModel.id == binding_id,
                BotExecutionIdentityBindingModel.env == env,
                BotExecutionIdentityBindingModel.status
                == ExecutionIdentityStatus.PENDING.value,
            ).with_for_update().one_or_none()
            if pending is None:
                raise ExecutionIdentityNotFoundError("Pending binding not found")
            session.query(BotModel).filter(
                BotModel.id == pending.bot_pk, BotModel.env == env
            ).with_for_update().one()
            active_rows = self._state_query(
                session,
                bot_pk=int(pending.bot_pk),
                status=ExecutionIdentityStatus.ACTIVE,
            ).with_for_update().all()
            for active in active_rows:
                active.status = ExecutionIdentityStatus.SUPERSEDED.value
                active.modifier_id = modifier_id
            pending.status = ExecutionIdentityStatus.ACTIVE.value
            pending.credential_status = "ISSUED"
            pending.modifier_id = modifier_id
            session.flush()
            result = _record(pending)
        return result

    def mark_failed(
        self, *, binding_id: int, failure_reason: str, modifier_id: str
    ) -> None:
        with self._db.transactional_orm_session() as session:
            row = session.query(BotExecutionIdentityBindingModel).filter(
                BotExecutionIdentityBindingModel.id == binding_id,
                BotExecutionIdentityBindingModel.status
                == ExecutionIdentityStatus.PENDING.value,
            ).with_for_update().one_or_none()
            if row is None:
                return
            row.status = ExecutionIdentityStatus.FAILED.value
            row.failure_reason = failure_reason[:1024]
            row.modifier_id = modifier_id
