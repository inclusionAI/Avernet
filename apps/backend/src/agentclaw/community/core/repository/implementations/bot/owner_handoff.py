"""Atomic local transitions; external credential work stays outside DB locks."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

from injector import inject
from sqlalchemy import func, or_

from agentclaw.community.core.bot_collaborator.models import (
    BotCollaboratorModel,
    BotCollabLockModel,
)
from agentclaw.community.core.execution_identity.models import (
    BotExecutionIdentityBindingModel,
)
from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    HandoffError,
    OwnerHandoffRepositoryProtocol,
)
from agentclaw.community.core.bot_management.owner_handoff.models import (
    OwnerHandoffModel,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderApproverModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env

TERMINAL = {"SUCCEEDED", "CANCELLED"}
FACTS = {
    "old_credential_id",
    "credential_id",
    "authorization_id",
    "agent_id",
    "error_code",
}


def _dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class OwnerHandoffRepository(OwnerHandoffRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin):
        self._db = db

    @staticmethod
    def _query(db):
        return db.query(OwnerHandoffModel).filter(
            OwnerHandoffModel.env == get_current_env()
        )

    @staticmethod
    def _bot(db, pk):
        bot = (
            db.query(BotModel)
            .filter(
                BotModel.id == pk,
                BotModel.env == get_current_env(),
                BotModel.is_delete == 0,
            )
            .with_for_update()
            .one_or_none()
        )
        if bot is None:
            raise HandoffError("BOT_NOT_FOUND")
        return bot

    @staticmethod
    def _bindings(db, pk):
        return (
            db.query(BotExecutionIdentityBindingModel)
            .filter(
                BotExecutionIdentityBindingModel.bot_pk == pk,
                BotExecutionIdentityBindingModel.env == get_current_env(),
                BotExecutionIdentityBindingModel.status.in_(["ACTIVE", "PENDING"]),
            )
            .with_for_update()
            .all()
        )

    @staticmethod
    def _assert_orders(db, bot):
        # Friend requests carry opaque request IDs, not a stable Bot PK. Until
        # BCN supplies one, conservatively block all pending friend approvals
        # assigned to this Owner. Never guess which Bot an opaque ID identifies.
        pending = (
            db.query(WorkOrderModel)
            .filter(
                WorkOrderModel.env == get_current_env(),
                WorkOrderModel.status.in_(["PENDING", "PROCESSING"]),
                or_(
                    (WorkOrderModel.biz_type == "BOT_COLLABORATOR")
                    & (WorkOrderModel.biz_id == bot.bot_id),
                    (WorkOrderModel.biz_type == "BOT_FRIEND")
                    & or_(
                        WorkOrderModel.reviewer_user_id == bot.owner_id,
                        WorkOrderModel.id.in_(
                            db.query(WorkOrderApproverModel.work_order_id).filter(
                                WorkOrderApproverModel.approver_user_id == bot.owner_id,
                                WorkOrderApproverModel.env == get_current_env(),
                                WorkOrderApproverModel.status == "PENDING",
                            )
                        ),
                        WorkOrderModel.status == "PROCESSING",
                    ),
                ),
            )
            .first()
        )
        if pending is not None:
            raise HandoffError("BOT_APPROVAL_PENDING")

    def begin(
        self,
        *,
        bot_pk,
        actor_id,
        new_owner_id,
        new_owner_name,
        request_key,
        administrators,
    ):
        with self._db.orm_session() as db:
            bot = self._bot(db, bot_pk)
            prior = (
                self._query(db)
                .filter(
                    OwnerHandoffModel.bot_pk == bot_pk,
                    OwnerHandoffModel.request_key == request_key,
                )
                .one_or_none()
            )
            if prior is not None:
                if prior.actor_id != actor_id or prior.new_owner_id != new_owner_id:
                    raise HandoffError("IDEMPOTENCY_CONFLICT")
                return _dict(prior)
            if actor_id != bot.owner_id and actor_id not in administrators:
                raise HandoffError("OWNER_REQUIRED")
            if bot.bot_type != "service" or bot.bot_id == "default":
                raise HandoffError("SERVICE_BOT_REQUIRED")
            if not bot.entity_id or new_owner_id == bot.owner_id:
                raise HandoffError("INVALID_OWNER_HANDOFF")
            ext = json.loads(bot.ext or "{}")
            if ext.get("owner_handoff"):
                raise HandoffError("BOT_OWNER_HANDOFF_IN_PROGRESS")
            self._assert_orders(db, bot)
            bindings = self._bindings(db, bot_pk)
            if len(bindings) > 1 or any(b.status == "PENDING" for b in bindings):
                raise HandoffError("EXECUTION_IDENTITY_CONFLICT")
            lock_key = f"{bot.bot_id}:{bot.entity_id}"
            if (
                db.query(BotCollabLockModel)
                .filter(
                    BotCollabLockModel.lock_key == lock_key,
                    BotCollabLockModel.env == get_current_env(),
                )
                .with_for_update()
                .first()
            ):
                raise HandoffError("BOT_EDIT_LOCKED")
            operation_id = uuid4().hex
            binding = bindings[0] if bindings else None
            row = OwnerHandoffModel(
                operation_id=operation_id,
                bot_pk=bot_pk,
                bot_id=bot.bot_id,
                entity_id=bot.entity_id,
                old_owner_id=bot.owner_id,
                new_owner_id=new_owner_id,
                new_owner_name=new_owner_name,
                actor_id=actor_id,
                request_key=request_key,
                phase="QUEUED",
                execution_workno=binding.execution_workno if binding else new_owner_id,
                binding_id=binding.id if binding else None,
                env=get_current_env(),
            )
            db.add(row)
            db.add(
                BotCollabLockModel(
                    lock_key=lock_key,
                    holder_user_id=f"handoff:{operation_id}",
                    env=get_current_env(),
                )
            )
            ext["owner_handoff"] = operation_id
            bot.ext = json.dumps(ext)
            db.flush()
            return _dict(row)

    def get(self, operation_id):
        with self._db.orm_session() as db:
            row = (
                self._query(db)
                .filter(OwnerHandoffModel.operation_id == operation_id)
                .one_or_none()
            )
            if row is None:
                raise HandoffError("HANDOFF_NOT_FOUND")
            return _dict(row)

    def claim(self, operation_id, worker):
        with self._db.orm_session() as db:
            now = db.query(func.now()).scalar()
            count = (
                self._query(db)
                .filter(
                    OwnerHandoffModel.operation_id == operation_id,
                    OwnerHandoffModel.phase.notin_(TERMINAL),
                    or_(
                        OwnerHandoffModel.lease_until.is_(None),
                        OwnerHandoffModel.lease_until < now,
                    ),
                )
                .update(
                    {
                        OwnerHandoffModel.worker: worker,
                        OwnerHandoffModel.lease_until: now + timedelta(seconds=120),
                    },
                    synchronize_session=False,
                )
            )
            if count != 1:
                return None
            return _dict(
                self._query(db)
                .filter(OwnerHandoffModel.operation_id == operation_id)
                .one()
            )

    def advance(self, operation_id, worker, phase, *, release=True, **facts):
        if not facts.keys() <= FACTS:
            raise ValueError("Only non-secret handoff facts may be persisted")
        with self._db.orm_session() as db:
            values = {
                **facts,
                "phase": phase,
                "worker": None if release else worker,
                "lease_until": None
                if release
                else db.query(func.now()).scalar() + timedelta(seconds=120),
            }
            count = (
                self._query(db)
                .filter(
                    OwnerHandoffModel.operation_id == operation_id,
                    OwnerHandoffModel.worker == worker,
                )
                .update(values, synchronize_session=False)
            )
            if count != 1:
                raise HandoffError("HANDOFF_LEASE_LOST")

    @staticmethod
    def _unfreeze(db, row, bot):
        ext = json.loads(bot.ext or "{}")
        if ext.get("owner_handoff") != row.operation_id:
            raise HandoffError("HANDOFF_MARKER_CONFLICT")
        del ext["owner_handoff"]
        bot.ext = json.dumps(ext)
        count = (
            db.query(BotCollabLockModel)
            .filter(
                BotCollabLockModel.lock_key == f"{row.bot_id}:{row.entity_id}",
                BotCollabLockModel.holder_user_id == f"handoff:{row.operation_id}",
                BotCollabLockModel.env == row.env,
            )
            .delete(synchronize_session=False)
        )
        if count != 1:
            raise HandoffError("HANDOFF_LOCK_CONFLICT")

    def finish(self, operation_id, worker):
        with self._db.orm_session() as db:
            row = (
                self._query(db)
                .filter(OwnerHandoffModel.operation_id == operation_id)
                .with_for_update()
                .one()
            )
            if row.worker != worker or row.phase != "COMMITTING_OWNER":
                raise HandoffError("HANDOFF_LEASE_LOST")
            bot = self._bot(db, row.bot_pk)
            if (
                bot.owner_id != row.old_owner_id
                or bot.entity_id != row.entity_id
                or bot.bot_id != row.bot_id
            ):
                raise HandoffError("BOT_IDENTITY_CONFLICT")
            self._assert_orders(db, bot)
            bindings = self._bindings(db, row.bot_pk)
            if row.binding_id is None:
                if bindings:
                    raise HandoffError("EXECUTION_IDENTITY_CONFLICT")
            else:
                if (
                    len(bindings) != 1
                    or bindings[0].id != row.binding_id
                    or bindings[0].execution_workno != row.execution_workno
                    or bindings[0].status != "ACTIVE"
                ):
                    raise HandoffError("EXECUTION_IDENTITY_CONFLICT")
                binding = bindings[0]
                binding.credential_id = row.credential_id
                binding.authorization_id = row.authorization_id
                binding.agent_id = row.agent_id
                binding.credential_status = "ISSUED"
            self._unfreeze(db, row, bot)
            bot.owner_id = row.new_owner_id
            bot.owner_name = row.new_owner_name
            bot.modifier_id = row.actor_id
            db.query(BotCollaboratorModel).filter(
                BotCollaboratorModel.bot_pk == row.bot_pk,
                BotCollaboratorModel.user_id == row.old_owner_id,
                BotCollaboratorModel.env == row.env,
            ).delete(synchronize_session=False)
            row.phase = "SUCCEEDED"
            row.error_code = None
            row.worker = None
            row.lease_until = None

    def cancel(self, operation_id, actor_id, administrators):
        with self._db.orm_session() as db:
            row = (
                self._query(db)
                .filter(OwnerHandoffModel.operation_id == operation_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise HandoffError("HANDOFF_NOT_FOUND")
            if actor_id != row.actor_id and actor_id not in administrators:
                raise HandoffError("OWNER_REQUIRED")
            if row.phase != "QUEUED" or row.worker is not None:
                raise HandoffError("HANDOFF_CANNOT_CANCEL")
            self._unfreeze(db, row, self._bot(db, row.bot_pk))
            row.phase = "CANCELLED"

    def recoverable(self):
        with self._db.orm_session() as db:
            return [
                _dict(row)
                for row in self._query(db)
                .execution_options(skip_avernet_tenant_guard=True)
                .filter(OwnerHandoffModel.phase.notin_(TERMINAL))
                .order_by(OwnerHandoffModel.gmt_modified)
                .limit(200)
                .all()
            ]
