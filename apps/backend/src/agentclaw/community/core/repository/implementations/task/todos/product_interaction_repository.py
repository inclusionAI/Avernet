"""Tenant-scoped persistence for Todo Product interactions."""

from __future__ import annotations

from injector import inject
from sqlalchemy import asc, desc

from agentclaw.community.core.repository.protocols.task.todo_product_interactions import (
    TodoProductInteractionRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoModel,
    TodoProductInteractionModel,
)
from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.task.todos.errors import (
    TodoNotFoundError,
    TodoProductInteractionInFlightError,
    TodoProductInteractionNotCancellableError,
    TodoProductInteractionTerminalTodoError,
    TodoProductInteractionTransitionError,
)
from agentclaw.community.core.task.todos.models import TodoStatus
from agentclaw.community.core.task.todos.product_interaction_models import (
    BLOCKING_PRODUCT_INTERACTION_STATES,
    TodoProductInteraction,
    TodoProductInteractionDeliveryState,
    TodoProductInteractionKind,
    TodoProductInteractionResponse,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


_TERMINAL_TODO_STATUSES = {
    TodoStatus.COMPLETED,
    TodoStatus.CANCELLED,
}


class TodoProductInteractionRepository(TodoProductInteractionRepositoryProtocol):
    """Serialize local interaction creation through the owning Todo row."""

    @inject
    def __init__(self, db) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    @classmethod
    def _scope(cls, query):
        return query.filter(
            TodoProductInteractionModel.env == get_current_env(),
            TodoProductInteractionModel.avernet_tenant
            == get_current_avernet_tenant(),
        )

    @classmethod
    def _interaction_query(cls, db, *, todo_id: str):
        return cls._scope(
            db.query(TodoProductInteractionModel).filter(
                TodoProductInteractionModel.todo_id == todo_id
            )
        )

    @classmethod
    def _require_interaction(
        cls, db, *, todo_id: str, interaction_id: str, lock: bool = False
    ) -> TodoProductInteractionModel:
        query = cls._interaction_query(db, todo_id=todo_id).filter(
            TodoProductInteractionModel.interaction_id == interaction_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise TodoNotFoundError("Product interaction not found")
        return row

    def create_interaction_single_flight(
        self,
        *,
        todo_id: str,
        interaction_id: str,
        kind: TodoProductInteractionKind,
        actor_id: str,
        actor_name: str | None,
        message: str,
        product_collet_id: int,
        reply_to_interaction_id: str | None = None,
    ) -> TodoProductInteraction:
        with self._transaction() as db:
            self._lock_ready_todo(db, todo_id)
            blocking = (
                self._interaction_query(db, todo_id=todo_id)
                .filter(
                    TodoProductInteractionModel.interaction_kind
                    == TodoProductInteractionKind.HUMAN_MESSAGE.value,
                    TodoProductInteractionModel.delivery_state.in_(
                        state.value for state in BLOCKING_PRODUCT_INTERACTION_STATES
                    ),
                )
                .one_or_none()
            )
            if blocking is not None:
                raise TodoProductInteractionInFlightError(blocking.interaction_id)
            latest = (
                self._interaction_query(db, todo_id=todo_id)
                .order_by(TodoProductInteractionModel.id.desc())
                .first()
            )
            if reply_to_interaction_id is not None and (
                latest is None or latest.interaction_id != reply_to_interaction_id
            ):
                raise ValidationError("reply target is not the latest interaction")
            row = self._model(
                interaction_id=interaction_id,
                todo_id=todo_id,
                kind=kind,
                actor_id=actor_id,
                actor_name=actor_name,
                message=message,
                product_collet_id=product_collet_id,
                target_interaction_id=reply_to_interaction_id,
            )
            db.add(row)
            db.flush()
            return row.to_record()

    def request_cancel_and_create_stop(
        self,
        *,
        todo_id: str,
        interaction_id: str,
        stop_interaction_id: str,
        actor_id: str,
        actor_name: str | None,
        message: str,
        product_collet_id: int,
    ) -> TodoProductInteraction:
        with self._transaction() as db:
            self._lock_ready_todo(db, todo_id)
            target = self._require_interaction(
                db, todo_id=todo_id, interaction_id=interaction_id, lock=True
            )
            if target.delivery_state not in {
                TodoProductInteractionDeliveryState.SENDING.value,
                TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN.value,
                TodoProductInteractionDeliveryState.CANCEL_REQUESTED.value,
            }:
                raise TodoProductInteractionNotCancellableError(target.delivery_state)
            target.delivery_state = (
                TodoProductInteractionDeliveryState.CANCEL_REQUESTED.value
            )
            existing = self._interaction_query(db, todo_id=todo_id).filter(
                TodoProductInteractionModel.interaction_id == stop_interaction_id
            ).one_or_none()
            if existing is not None:
                db.flush()
                return existing.to_record()
            row = self._model(
                interaction_id=stop_interaction_id,
                todo_id=todo_id,
                kind=TodoProductInteractionKind.STOP_REQUEST,
                actor_id=actor_id,
                actor_name=actor_name,
                message=message,
                product_collet_id=product_collet_id,
                target_interaction_id=interaction_id,
            )
            db.add(row)
            db.flush()
            return row.to_record()

    def get_interaction(
        self, *, todo_id: str, interaction_id: str
    ) -> TodoProductInteraction:
        with self._db.orm_session() as db:
            return self._require_interaction(
                db, todo_id=todo_id, interaction_id=interaction_id
            ).to_record()

    def list_interactions(
        self, *, todo_id: str, page: int, page_size: int
    ) -> tuple[TodoProductInteraction, ...]:
        if page <= 0 or page_size <= 0 or page_size > 100:
            raise ValueError("invalid Product interaction pagination")
        with self._db.orm_session() as db:
            rows = (
                self._interaction_query(db, todo_id=todo_id)
                .order_by(
                    asc(TodoProductInteractionModel.gmt_create),
                    asc(TodoProductInteractionModel.id),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return tuple(row.to_record() for row in rows)

    def get_blocking_interaction(self, *, todo_id: str) -> TodoProductInteraction | None:
        with self._db.orm_session() as db:
            row = (
                self._interaction_query(db, todo_id=todo_id)
                .filter(
                    TodoProductInteractionModel.interaction_kind
                    == TodoProductInteractionKind.HUMAN_MESSAGE.value,
                    TodoProductInteractionModel.delivery_state.in_(
                        state.value for state in BLOCKING_PRODUCT_INTERACTION_STATES
                    ),
                )
                .order_by(asc(TodoProductInteractionModel.id))
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def save_rollout_fence(
        self,
        *,
        interaction_id: str,
        collet_rollout_id: int,
        expected_revision: int,
    ) -> TodoProductInteraction:
        with self._transaction() as db:
            row = db.query(TodoProductInteractionModel).filter(
                TodoProductInteractionModel.interaction_id == interaction_id,
                TodoProductInteractionModel.env == get_current_env(),
                TodoProductInteractionModel.avernet_tenant
                == get_current_avernet_tenant(),
            ).one_or_none()
            if row is None:
                raise TodoNotFoundError("Product interaction not found")
            row.collet_rollout_id = collet_rollout_id
            row.expected_revision = expected_revision
            db.flush()
            return row.to_record()

    def mark_delivery_unknown(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction:
        return self._transition(
            interaction_id,
            TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN,
            {
                TodoProductInteractionDeliveryState.SENDING,
                TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN,
                TodoProductInteractionDeliveryState.CANCEL_REQUESTED,
            },
            failure_code=failure_code,
        )

    def complete_interaction(
        self,
        *,
        interaction_id: str,
        response_type: str,
        response_message: str,
    ) -> TodoProductInteraction:
        allowed = {
            TodoProductInteractionDeliveryState.SENDING,
            TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN,
            TodoProductInteractionDeliveryState.CANCEL_REQUESTED,
        }
        with self._transaction() as db:
            row = db.query(TodoProductInteractionModel).filter(
                TodoProductInteractionModel.interaction_id == interaction_id,
                TodoProductInteractionModel.env == get_current_env(),
                TodoProductInteractionModel.avernet_tenant
                == get_current_avernet_tenant(),
            ).one_or_none()
            if row is None:
                raise TodoNotFoundError("Product interaction not found")
            current = TodoProductInteractionDeliveryState(row.delivery_state)
            if current not in allowed:
                raise TodoProductInteractionTransitionError(current.value, "succeeded")
            row.delivery_state = TodoProductInteractionDeliveryState.SUCCEEDED.value
            row.response_type = response_type
            row.response_message = response_message
            row.failure_code = None
            db.flush()
            return row.to_record()

    def fail_interaction(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction:
        return self._transition(
            interaction_id,
            TodoProductInteractionDeliveryState.FAILED,
            {
                TodoProductInteractionDeliveryState.SENDING,
                TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN,
                TodoProductInteractionDeliveryState.CANCEL_REQUESTED,
            },
            failure_code=failure_code,
        )

    def mark_cancelled(
        self, *, todo_id: str, interaction_id: str
    ) -> TodoProductInteraction:
        return self._transition(
            interaction_id,
            TodoProductInteractionDeliveryState.CANCELLED,
            {
                TodoProductInteractionDeliveryState.SENDING,
                TodoProductInteractionDeliveryState.DELIVERY_UNKNOWN,
                TodoProductInteractionDeliveryState.CANCEL_REQUESTED,
            },
        )

    def _transition(
        self,
        interaction_id: str,
        new_state: TodoProductInteractionDeliveryState,
        allowed: set[TodoProductInteractionDeliveryState],
        *,
        failure_code: str | None = None,
    ) -> TodoProductInteraction:
        with self._transaction() as db:
            row = db.query(TodoProductInteractionModel).filter(
                TodoProductInteractionModel.interaction_id == interaction_id,
                TodoProductInteractionModel.env == get_current_env(),
                TodoProductInteractionModel.avernet_tenant
                == get_current_avernet_tenant(),
            ).one_or_none()
            if row is None:
                raise TodoNotFoundError("Product interaction not found")
            current = TodoProductInteractionDeliveryState(row.delivery_state)
            if current not in allowed:
                raise TodoProductInteractionTransitionError(current, new_state)
            row.delivery_state = new_state.value
            if failure_code is not None:
                row.failure_code = failure_code
            db.flush()
            return row.to_record()

    def _lock_ready_todo(self, db, todo_id: str) -> TodoModel:
        row = (
            db.query(TodoModel)
            .filter(
                TodoModel.todo_id == todo_id,
                TodoModel.env == get_current_env(),
                TodoModel.avernet_tenant == get_current_avernet_tenant(),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise TodoNotFoundError(f"Todo {todo_id} not found")
        if TodoStatus(row.status) in _TERMINAL_TODO_STATUSES:
            raise TodoProductInteractionTerminalTodoError(row.status)
        return row

    @staticmethod
    def _model(
        *,
        interaction_id: str,
        todo_id: str,
        kind: TodoProductInteractionKind,
        actor_id: str,
        actor_name: str | None,
        message: str,
        product_collet_id: int,
        target_interaction_id: str | None = None,
    ) -> TodoProductInteractionModel:
        return TodoProductInteractionModel(
            interaction_id=interaction_id,
            todo_id=todo_id,
            interaction_kind=kind.value,
            target_interaction_id=target_interaction_id,
            product_collet_id=product_collet_id,
            actor_id=actor_id,
            actor_name=actor_name,
            request_message=message,
            delivery_state=TodoProductInteractionDeliveryState.SENDING.value,
            env=get_current_env(),
            avernet_tenant=get_current_avernet_tenant(),
        )
