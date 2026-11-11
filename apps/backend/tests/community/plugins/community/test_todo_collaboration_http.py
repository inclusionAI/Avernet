"""HTTP adapter tests for the B-side Todo collaboration boundary."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import agentclaw.community.plugins.community.todo_collaboration as todo_collaboration_module
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContextRequest,
    TodoCollaborationError,
    TodoCollaborationEventRequest,
)
from agentclaw.community.plugins.community.todo_collaboration import (
    HttpTodoCollaborationPlugin,
)

_CONTEXT_ID = "context_todo_1"


def _context_request() -> TodoCollaborationContextRequest:
    return TodoCollaborationContextRequest(
        task_id="todo_1",
        task_title="Prepare report",
        collaboration_goal="Write the report",
        collaborators=("user_1", "user_2"),
        owner_id="user_1",
        bot_id="bot_1",
    )


def _event_payload(method: str, path: str) -> dict[str, Any]:
    if method == "POST" and path == "/api/v1/collaboration/task/context_collab":
        return {"data": {"collab_context_id": _CONTEXT_ID}}
    if (
        method == "POST"
        and path == f"/api/v1/collaboration/task/context_collab/{_CONTEXT_ID}/events"
    ):
        return {
            "data": {
                "event_id": "event_3",
                "event_seq": 3,
                "event_type": "human_interaction",
                "actor_id": "user_1",
                "actor_type": "human",
                "content": "Please revise",
                "execution_id": "execution_1",
                "context_version": "v2",
                "artifacts": [
                    {
                        "artifact_id": "artifact_1",
                        "name": "report.md",
                        "address": "/files/report.md",
                    }
                ],
            }
        }
    return {
        "events": [
            {
                "event_id": "event_2",
                "event_seq": 2,
                "event_type": "execution.status",
                "actor_id": "bot_1",
                "actor_type": "bot",
                "message": "done",
                "created_time": "2026-11-10T08:00:00Z",
                "status": "pending_acceptance",
            }
        ]
    }


@pytest.mark.parametrize(
    ("operation", "expected_method", "expected_path"),
    [
        ("context", "POST", "/api/v1/collaboration/task/context_collab"),
        (
            "append",
            "POST",
            f"/api/v1/collaboration/task/context_collab/{_CONTEXT_ID}/events",
        ),
        ("list", "GET", f"/api/v1/collaboration/task/context_collab/{_CONTEXT_ID}"),
    ],
)
def test_http_plugin_uses_the_confirmed_b_paths_and_parsing_contract(
    monkeypatch, operation: str, expected_method: str, expected_path: str
) -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        return httpx.Response(
            200, json=_event_payload(request.method, request.url.path)
        )

    real_client = httpx.Client

    def client_with_transport(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(
        todo_collaboration_module.httpx, "Client", client_with_transport
    )
    plugin = HttpTodoCollaborationPlugin(base_url="http://collaboration.test")

    if operation == "context":
        result = plugin.create_context(_context_request())
        assert result.collab_context_id == _CONTEXT_ID
        assert requests[0][2] == {
            "task_id": "todo_1",
            "task_title": "Prepare report",
            "collaboration_goal": "Write the report",
            "collaborators": ["user_1", "user_2"],
            "owner_id": "user_1",
            "bot_id": "bot_1",
        }
    elif operation == "append":
        request = TodoCollaborationEventRequest(
            event_type="human_interaction",
            actor_id="user_1",
            content="Please revise",
            boundary_event_seq=2,
            payload={"author_name": "User One"},
        )
        event = plugin.append_event(collab_context_id=_CONTEXT_ID, request=request)
        assert requests[0][2] == {
            "event_type": "human_interaction",
            "actor_id": "user_1",
            "content": "Please revise",
            "boundary_event_seq": 2,
            "author_name": "User One",
        }
        assert event.event_id == "event_3"
        assert event.event_seq == 3
        assert event.artifacts[0].url == "/files/report.md"
    else:
        events = plugin.list_events(collab_context_id=_CONTEXT_ID)
        assert events[0].event_id == "event_2"
        assert events[0].content == "done"
        assert events[0].created_at == "2026-11-10T08:00:00Z"
        assert events[0].status == "pending_acceptance"

    assert requests[0][0] == expected_method
    assert requests[0][1] == expected_path


def test_http_plugin_rejects_malformed_event_list(monkeypatch) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"unexpected": []}})

    real_client = httpx.Client

    def client_with_transport(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(
        todo_collaboration_module.httpx, "Client", client_with_transport
    )
    plugin = HttpTodoCollaborationPlugin(base_url="http://collaboration.test")

    with pytest.raises(TodoCollaborationError, match="event list"):
        plugin.list_events(collab_context_id=_CONTEXT_ID)
