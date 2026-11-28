from __future__ import annotations

import pytest

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoModel,
)
from agentclaw.community.core.repository.implementations.task.todos.product_interaction_repository import (
    TodoProductInteractionRepository,
)
from agentclaw.community.core.task.todos.errors import (
    TodoProductInteractionInFlightError,
    TodoProductInteractionNotCancellableError,
    TodoProductInteractionTransitionError,
)
from agentclaw.community.core.task.todos.models import TodoStatus
from agentclaw.community.core.task.todos.product_interaction_models import (
    TodoProductInteractionDeliveryState,
    TodoProductInteractionKind,
    build_product_interaction_id,
    build_stop_request_id,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def _todo_id(db, todo_id: str = "todo_1") -> str:
    with db.orm_session() as session:
        session.add(
            TodoModel(
                todo_id=todo_id,
                space_id=7,
                title="Product Todo",
                description="",
                template_id="team_1",
                template_version_id="rev_1",
                template_name_snapshot="Product Todo",
                participant_bindings_json="[]",
                bot_id="team_1",
                bot_name="Product team",
                status=TodoStatus.IN_PROGRESS.value,
                product_collet_id=22,
                created_by="human_1",
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
        )
    return todo_id


@pytest.fixture
def repository(db):
    return TodoProductInteractionRepository(db)


def test_create_is_single_flight_and_rejects_wrong_reply_target(
    db, repository
) -> None:
    todo_id = _todo_id(db)
    interaction_id = build_product_interaction_id()

    created = repository.create_interaction_single_flight(
        todo_id=todo_id,
        interaction_id=interaction_id,
        kind=TodoProductInteractionKind.HUMAN_MESSAGE,
        actor_id="human_1",
        actor_name=None,
        message="请继续处理",
        product_collet_id=22,
    )
    assert created.delivery_state is TodoProductInteractionDeliveryState.SENDING

    with pytest.raises(TodoProductInteractionInFlightError) as exc_info:
        repository.create_interaction_single_flight(
            todo_id=todo_id,
            interaction_id=build_product_interaction_id(),
            kind=TodoProductInteractionKind.HUMAN_MESSAGE,
            actor_id="human_1",
            actor_name=None,
            message="第二条",
            product_collet_id=22,
        )
    assert exc_info.value.interaction_id == interaction_id


def test_cancel_uses_one_deterministic_stop_request_and_is_idempotent(
    db, repository
) -> None:
    todo_id = _todo_id(db, "todo_2")
    target_id = build_product_interaction_id()
    repository.create_interaction_single_flight(
        todo_id=todo_id,
        interaction_id=target_id,
        kind=TodoProductInteractionKind.HUMAN_MESSAGE,
        actor_id="human_1",
        actor_name=None,
        message="请继续处理",
        product_collet_id=22,
    )

    stop = repository.request_cancel_and_create_stop(
        todo_id=todo_id,
        interaction_id=target_id,
        stop_interaction_id=build_stop_request_id(target_id),
        actor_id="human_1",
        actor_name=None,
        message="请停止当前待办执行。",
        product_collet_id=22,
    )
    repeated = repository.request_cancel_and_create_stop(
        todo_id=todo_id,
        interaction_id=target_id,
        stop_interaction_id=build_stop_request_id(target_id),
        actor_id="human_1",
        actor_name=None,
        message="请停止当前待办执行。",
        product_collet_id=22,
    )

    assert stop.interaction_id == build_stop_request_id(target_id)
    assert stop.target_interaction_id == target_id
    assert stop.kind is TodoProductInteractionKind.STOP_REQUEST
    assert repeated.interaction_id == stop.interaction_id
    assert repository.get_interaction(
        todo_id=todo_id, interaction_id=target_id
    ).delivery_state is TodoProductInteractionDeliveryState.CANCEL_REQUESTED
    blocking = repository.get_blocking_interaction(todo_id=todo_id)
    assert blocking is not None
    assert blocking.interaction_id == target_id
    with pytest.raises(TodoProductInteractionInFlightError) as exc_info:
        repository.create_interaction_single_flight(
            todo_id=todo_id,
            interaction_id=build_product_interaction_id(),
            kind=TodoProductInteractionKind.HUMAN_MESSAGE,
            actor_id="human_1",
            actor_name=None,
            message="停止等待期间的新消息",
            product_collet_id=22,
        )
    assert exc_info.value.interaction_id == target_id


def test_state_transitions_fail_closed_after_terminal_state(db, repository) -> None:
    todo_id = _todo_id(db, "todo_3")
    interaction_id = build_product_interaction_id()
    repository.create_interaction_single_flight(
        todo_id=todo_id,
        interaction_id=interaction_id,
        kind=TodoProductInteractionKind.HUMAN_MESSAGE,
        actor_id="human_1",
        actor_name=None,
        message="请求",
        product_collet_id=22,
    )
    completed = repository.complete_interaction(
        interaction_id=interaction_id,
        response_type="RE_DO",
        response_message="已重做",
    )
    assert completed.delivery_state is TodoProductInteractionDeliveryState.SUCCEEDED

    with pytest.raises(TodoProductInteractionTransitionError):
        repository.complete_interaction(
            interaction_id=interaction_id,
            response_type="RE_DO",
            response_message="重复回写",
        )
    with pytest.raises(TodoProductInteractionNotCancellableError):
        repository.request_cancel_and_create_stop(
            todo_id=todo_id,
            interaction_id=interaction_id,
            stop_interaction_id=build_stop_request_id(interaction_id),
            actor_id="human_1",
            actor_name=None,
            message="停止",
            product_collet_id=22,
        )
