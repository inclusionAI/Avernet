from __future__ import annotations

import inspect
from typing import Any

import pytest

from agentclaw.community.adapters.http.openapi_v1.task.todos.product_interaction_schemas import (
    CreateTodoProductInteractionRequest,
)
from agentclaw.community.adapters.http.openapi_v1.task.todos.router import (
    cancel_todo_product_interaction,
    create_todo_product_interaction,
    list_todo_product_interactions,
)
from agentclaw.community.core.task.todos.product_interaction_models import (
    TodoProductInteraction,
    TodoProductInteractionDeliveryState,
    TodoProductInteractionKind,
    TodoProductInteractionPage,
    TodoProductInteractionResponse,
)


def _request() -> Any:
    from fastapi import Request

    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/openapi/v1/collaboration/tasks/todos/todo_1/product-interactions",
        }
    )
    request.state.trace_id = "trace-todo"
    return request


def _interaction(
    interaction_id: str = "int_1",
    delivery_state: TodoProductInteractionDeliveryState = (
        TodoProductInteractionDeliveryState.SUCCEEDED
    ),
    kind: TodoProductInteractionKind = TodoProductInteractionKind.HUMAN_MESSAGE,
    target: str | None = None,
) -> TodoProductInteraction:
    return TodoProductInteraction(
        interaction_id=interaction_id,
        todo_id="todo_1",
        kind=kind,
        actor_id="human_1",
        actor_name="Human One",
        request_message="请重新执行",
        delivery_state=delivery_state,
        product_collet_id=22,
        collet_rollout_id=501,
        expected_revision=7,
        target_interaction_id=target,
        response=(
            TodoProductInteractionResponse("RE_DO", "已重新执行")
            if kind is TodoProductInteractionKind.HUMAN_MESSAGE
            else TodoProductInteractionResponse("SHUTDOWN", "已停止")
        ),
        failure_code=None,
        created_at="2026-11-27T08:00:00",
        updated_at="2026-11-27T08:00:01",
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_interactions(self, **kwargs):
        self.calls.append({"op": "list", **kwargs})
        return TodoProductInteractionPage(
            (_interaction(),), can_send=False, blocking_interaction_id="int_1"
        )

    async def send_interaction(self, **kwargs):
        self.calls.append({"op": "send", **kwargs})
        return _interaction()

    async def cancel_interaction(self, **kwargs):
        self.calls.append({"op": "cancel", **kwargs})
        return _interaction(
            interaction_id="int_1:shutdown:v1",
            kind=TodoProductInteractionKind.STOP_REQUEST,
            target="int_1",
        )


@pytest.mark.asyncio
async def test_list_route_keeps_actor_from_principal_and_returns_lock():
    service = _Service()

    result = await list_todo_product_interactions(
        todo_id="todo_1",
        request=_request(),
        principal={"user_id": "human_1"},
        user_id="human_1",
        page=2,
        page_size=10,
        service=service,
    )

    assert service.calls == [
        {
            "op": "list",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "page": 2,
            "page_size": 10,
        }
    ]
    assert result.data.can_send is False
    assert result.data.blocking_interaction_id == "int_1"
    assert result.data.items[0].response.type == "RE_DO"


@pytest.mark.asyncio
async def test_send_route_does_not_accept_request_or_runtime_identity():
    service = _Service()

    result = await create_todo_product_interaction(
        todo_id="todo_1",
        body=CreateTodoProductInteractionRequest(
            message="请重新执行", reply_to_interaction_id=None
        ),
        request=_request(),
        response=_make_response(),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert "interaction_id" not in service.calls[-1]
    assert "rollout_id" not in service.calls[-1]
    assert service.calls[-1]["message"] == "请重新执行"
    assert result.data.delivery_state == "succeeded"
    inspect.signature(create_todo_product_interaction)
    assert result.code == 201000


@pytest.mark.asyncio
async def test_cancel_route_addresses_one_local_stop_interaction():
    service = _Service()

    result = await cancel_todo_product_interaction(
        todo_id="todo_1",
        interaction_id="int_1",
        request=_request(),
        principal={"user_id": "human_1"},
        user_id="human_1",
        service=service,
    )

    assert service.calls == [
        {
            "op": "cancel",
            "todo_id": "todo_1",
            "actor_id": "human_1",
            "interaction_id": "int_1",
        }
    ]
    assert result.data.interaction_id == "int_1:shutdown:v1"
    assert result.data.target_interaction_id == "int_1"
    assert result.data.response.type == "SHUTDOWN"


def _make_response():
    from fastapi import Response

    return Response()
