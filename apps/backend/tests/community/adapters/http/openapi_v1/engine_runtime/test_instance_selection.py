"""Public UUID selectors route requests; they are not engine query filters."""

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import router
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.models import (
    router as models_router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.nodes import (
    router as nodes_router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine import (
    router as engine_router,
)
from agentclaw.community.core.engine_runtime.models import EngineResult
from .conftest import BOT


def test_session_file_contract_does_not_expose_instance_selection():
    from tests.community.adapters.http.openapi_v1.conftest import public_document

    paths = public_document()["paths"]
    file_paths = {
        path: item for path, item in paths.items()
        if path.startswith("/openapi/v1/bots/{bot_id}/sessions/{session_id}/files")
    }
    assert len(file_paths) == 6
    for item in file_paths.values():
        for operation in item.values():
            assert "device_uuid" not in {
                param["name"] for param in operation.get("parameters", [])
            }


@pytest.mark.parametrize(
    "method,path,body,payload",
    [
        ("GET", "/sessions", None, []),
        ("GET", "/sessions/favorites", None, []),
        ("GET", "/sessions/s1", None, {"id": "s1"}),
        ("GET", "/sessions/s1/messages", None, []),
        ("POST", "/sessions", {"title": "hello"}, {"id": "s1"}),
        ("PATCH", "/sessions/s1", {"title": "renamed"}, {"id": "s1"}),
        ("DELETE", "/sessions/s1", None, {}),
        ("DELETE", "/sessions/s1/messages", None, {}),
        ("PUT", "/sessions/s1/favorite", None, {}),
        ("DELETE", "/sessions/s1/favorite", None, {}),
    ],
)
def test_session_operations_forward_selector(
    make_client, relay, method, path, body, payload
):
    relay.set_bot_type("service")
    relay.set_active_engine("deepseek_harness")
    relay.results = [EngineResult(data=payload)]
    response = make_client(router).request(
        method,
        f"/openapi/v1/bots/{BOT}{path}",
        params={"device_uuid": "instance-B"},
        json=body,
    )
    assert response.status_code < 300, response.text
    assert relay.calls
    assert all(call["device_uuid"] == "instance-B" for call in relay.calls)
    assert all("device_uuid" not in (call["params"] or {}) for call in relay.calls)


@pytest.mark.parametrize(
    "route,path,payload",
    [
        (models_router, "/models", []),
        (nodes_router, "/nodes", []),
        (engine_router, "/engine/status", {}),
        (engine_router, "/engine/capabilities", {}),
    ],
)
def test_runtime_reads_follow_selected_instance(
    make_client, relay, route, path, payload
):
    relay.set_bot_type("service")
    relay.results = [EngineResult(data=payload)]
    response = make_client(route).get(
        f"/openapi/v1/bots/{BOT}{path}",
        params={"stage": "online", "device_uuid": "instance-B"},
    )
    assert response.status_code == 200, response.text
    assert relay.calls[-1]["device_uuid"] == "instance-B"
    assert relay.calls[-1]["stage"] == "online"


@pytest.mark.parametrize("selector", ["", "../another", "x" * 129])
def test_invalid_selector_never_reaches_relay(make_client, relay, selector):
    response = make_client(router).get(
        f"/openapi/v1/bots/{BOT}/sessions", params={"device_uuid": selector}
    )
    assert response.status_code == 422
    assert relay.calls == []


def test_friend_chat_cannot_silently_ignore_selector(make_client, relay):
    response = make_client(router).get(
        f"/openapi/v1/bots/{BOT}/sessions",
        params={"device_uuid": "instance-B", "f_user_id": "friend"},
    )
    assert response.status_code == 404
    assert relay.calls == []
