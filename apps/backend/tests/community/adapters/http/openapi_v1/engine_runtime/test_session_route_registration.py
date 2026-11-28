"""Session route composition must preserve CRUD/files and one collection read."""

from collections import Counter

from fastapi.routing import APIRoute

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.listing import (
    list_sessions,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.router import router


BASE = "/openapi/v1/bots/{bot_id}/sessions"


def test_session_routes_are_complete_and_registered_once():
    expected = {
        ("GET", ""), ("POST", ""), ("GET", "/favorites"),
        ("GET", "/{session_id}"), ("PATCH", "/{session_id}"),
        ("DELETE", "/{session_id}"),
        ("PUT", "/{session_id}/favorite"), ("DELETE", "/{session_id}/favorite"),
        ("POST", "/{session_id}/files/upload-intents"),
        ("POST", "/{session_id}/files/upload-complete"),
        ("GET", "/{session_id}/files/{resource_id}/materialize-status"),
        ("GET", "/{session_id}/files"),
        ("GET", "/{session_id}/files/{resource_id}/content"),
        ("DELETE", "/{session_id}/files/{resource_id}"),
        ("GET", "/{session_id}/messages"), ("DELETE", "/{session_id}/messages"),
    }
    actual = Counter(
        (method, route.path.removeprefix(BASE))
        for route in router.routes if isinstance(route, APIRoute)
        for method in route.methods
    )
    assert actual == Counter({operation: 1 for operation in expected})
    collection = [
        route for route in router.routes if isinstance(route, APIRoute)
        and route.path == BASE and "GET" in route.methods
    ]
    assert collection[0].endpoint is list_sessions
    assert {p.name for p in collection[0].dependant.query_params} >= {
        "source", "source_cursor", "agent_id", "session_key",
    }
