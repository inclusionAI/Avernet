from __future__ import annotations

import pytest
from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.task.todos.template_capability_router import (
    get_todo_template_capabilities,
    router,
)
from agentclaw.community.core.task.todos.template_capabilities import (
    TODO_TEMPLATE_CAPABILITIES,
)


def _request() -> Request:
    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/openapi/v1/collaboration/tasks/todo-template-capabilities",
        }
    )
    request.state.trace_id = "trace-template-capabilities"
    return request


class _CapabilityService:
    def __init__(self) -> None:
        self.calls = 0

    def get_capabilities(self):
        self.calls += 1
        return TODO_TEMPLATE_CAPABILITIES


@pytest.mark.asyncio
async def test_get_template_capabilities_returns_stable_supported_contract() -> None:
    service = _CapabilityService()

    payload = await get_todo_template_capabilities(
        request=_request(),
        user_id="user-template-editor",
        service=service,
    )

    assert payload.code == 200000
    assert payload.request_id == "trace-template-capabilities"
    assert payload.data.supported_schema_versions == ["1.0"]
    assert payload.data.participant_reference_sources == [
        "binding",
        "fixed",
        "todo_creator",
    ]
    assert payload.data.stage_kinds == ["bot", "human"]
    assert [item.model_dump() for item in payload.data.artifacts] == [
        {
            "artifact_type": "pull_request",
            "artifact_state": "created",
            "hard_gate_supported": True,
        }
    ]
    assert payload.data.features.model_dump() == {
        "branching": False,
        "parallel_stages": False,
        "late_binding": False,
    }
    assert service.calls == 1


def test_template_capability_route_is_todos_sibling_not_todo_wildcard() -> None:
    operations = {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
    }

    assert operations == {
        (
            "GET",
            "/openapi/v1/collaboration/tasks/todo-template-capabilities",
        )
    }
    assert all("/todos/" not in path for _method, path in operations)
