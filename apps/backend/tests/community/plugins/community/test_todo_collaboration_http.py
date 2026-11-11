"""HTTP adapter tests for Todo's B-side collaboration boundary."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import agentclaw.community.plugins.community.todo_collaboration as module
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationContextRequest,
    TodoCollaborationError,
    TodoCollaborationEventRequest,
)
from agentclaw.community.plugins.community.todo_collaboration import (
    HttpTodoCollaborationPlugin,
)

_CONTEXT_ID = "context_todo_1"
_BASE_PATH = "/api/v1/collaboration/task/context_collab"


def _context_request() -> TodoCollaborationContextRequest:
    return TodoCollaborationContextRequest(
        space_id="7",
        environment="",
        principal_id="user_1",
        product_kind="team_todo",
        workflow_kind="team_todo_execution",
        method_kind="Single",
        executor_id="bot_1",
        objective="Write the report",
        todo_id="todo_1",
    )


def _context_payload(
    *, status: str = "created", event_kind: str = "task.created"
) -> dict[str, Any]:
    return {
        "data": {
            "collab_context_id": _CONTEXT_ID,
            "context_version": 2,
            "current_snapshot": {
                "status": status,
                "progress": {"current_event_id": "event_2"},
            },
            "current_event": {"event_id": "event_2"},
            "task_progress": {
                "total_events": 1,
                "events": [
                    {
                        "event_id": "event_2",
                        "event_seq": 2,
                        "parent_event": "event_1",
                        "event_kind": event_kind,
                        "primitive_type": "Message",
                        "actor_id": "user_1",
                        "actor_type": "human",
                        "execution_id": "execution_1",
                        "context_version": 2,
                        "status": "active",
                        "summary": None,
                        "content": "Please revise",
                        "artifacts": [
                            {
                                "artifact_id": "artifact_1",
                                "name": "report.md",
                                "url": "/files/report.md",
                            }
                        ],
                        "created_at": "2026-11-10T08:00:00Z",
                    }
                ],
            },
        }
    }


def _install_transport(monkeypatch, responder):
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        return responder(request)

    real_client = httpx.Client

    def client_with_transport(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", client_with_transport)
    return requests


def test_create_context_uses_b_context_contract(monkeypatch) -> None:
    requests = _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(200, json=_context_payload()),
    )
    plugin = HttpTodoCollaborationPlugin(base_url="http://collaboration.test")

    result = plugin.create_context(_context_request())

    assert result.collab_context_id == _CONTEXT_ID
    assert result.status == "created"
    assert requests == [
        (
            "POST",
            _BASE_PATH,
            {
                "space": {"space_id": "7", "environment": ""},
                "principal_ref": {
                    "principal_type": "human",
                    "principal_id": "user_1",
                },
                "product_kind": "team_todo",
                "workflow_kind": "team_todo_execution",
                "method_kind": "Single",
                "executor_ref": {"agent_type": "bot", "agent_id": "bot_1"},
                "objective": "Write the report",
                "resource_refs": [
                    {
                        "resource_type": "todo",
                        "resource_id": "todo_1",
                        "revision": None,
                    }
                ],
            },
        )
    ]


def test_get_context_parses_full_event_projection(monkeypatch) -> None:
    requests = _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(200, json=_context_payload()),
    )
    plugin = HttpTodoCollaborationPlugin(base_url="http://collaboration.test")

    context = plugin.get_context(collab_context_id=_CONTEXT_ID)

    assert requests[0][:2] == ("GET", f"{_BASE_PATH}/{_CONTEXT_ID}")
    assert context.current_event_id == "event_2"
    assert context.events[0].event_type == "task.created"
    assert context.events[0].context_version == 2
    assert context.events[0].artifacts[0].url == "/files/report.md"


def test_run_context_posts_then_refreshes_context(monkeypatch) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"run_id": "run_1"}})
        return httpx.Response(200, json=_context_payload(status="in_progress"))

    requests = _install_transport(monkeypatch, responder)
    plugin = HttpTodoCollaborationPlugin(base_url="http://collaboration.test")

    context = plugin.run_context(collab_context_id=_CONTEXT_ID)

    assert context.status == "in_progress"
    assert [(method, path) for method, path, _ in requests] == [
        ("POST", f"{_BASE_PATH}/{_CONTEXT_ID}/run"),
        ("GET", f"{_BASE_PATH}/{_CONTEXT_ID}"),
    ]


def test_append_event_uses_new_protocol_and_returns_refreshed_event(
    monkeypatch,
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"event_id": "event_2"}})
        return httpx.Response(
            200,
            json=_context_payload(event_kind="user_interaction.posted"),
        )

    requests = _install_transport(monkeypatch, responder)
    plugin = HttpTodoCollaborationPlugin(base_url="http://collaboration.test")
    event_request = TodoCollaborationEventRequest(
        expected_version=1,
        event_kind="user_interaction.posted",
        primitive_type="Message",
        actor_id="user_1",
        actor_type="human",
        parent_event="event_1",
        visibility="workspace",
        payload={"content": "Please revise"},
    )

    event = plugin.append_event(
        collab_context_id=_CONTEXT_ID,
        request=event_request,
    )

    assert requests[0] == (
        "POST",
        f"{_BASE_PATH}/{_CONTEXT_ID}/events",
        {
            "expected_version": 1,
            "event_kind": "user_interaction.posted",
            "primitive_type": "Message",
            "actor_ref": {
                "principal_type": "human",
                "principal_id": "user_1",
            },
            "parent_event": "event_1",
            "visibility": "workspace",
            "payload": {"content": "Please revise"},
        },
    )
    assert requests[1][:2] == ("GET", f"{_BASE_PATH}/{_CONTEXT_ID}")
    assert event.event_type == "user_interaction.posted"
    assert event.content == "Please revise"


def test_http_plugin_rejects_malformed_context(monkeypatch) -> None:
    _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(200, json={"data": {"unexpected": []}}),
    )
    plugin = HttpTodoCollaborationPlugin(base_url="http://collaboration.test")

    with pytest.raises(TodoCollaborationError, match="collab_context_id"):
        plugin.get_context(collab_context_id=_CONTEXT_ID)
