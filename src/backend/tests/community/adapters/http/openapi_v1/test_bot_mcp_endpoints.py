"""Endpoint tests for the bot-scoped MCP group ``/openapi/v1/bots/{bot_id}/mcp``.

A minimal FastAPI app hosts the router with the caller principal overridden and
the state service bound to a mock — mirroring the other openapi_v1 endpoint
harnesses. The service's own state machine is covered in
``tests/community/core/mcp/services/test_bot_mcp_state_service.py``; here the
subject is the HTTP contract: shapes, status codes, and the masked 404s.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.bot_mcp.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.api.bot_mcp_state_service import BotMcpStateServiceProtocol
from agentclaw.community.core.mcp.errors import (
    McpBotServerNotFoundError,
    McpDefaultServerNotRemovableError,
    McpServerNotFoundError,
    McpSyncFailedError,
)

BOT = "b-1"
CODE = "mcp.weather"
BASE = f"/openapi/v1/bots/{BOT}/mcp"


def _entry(code=CODE, *, active=False, is_default=False):
    return {
        "server_code": code,
        "name": "Weather",
        "description": "d",
        "active": active,
        "is_default": is_default,
    }


@pytest.fixture
def state():
    m = MagicMock()
    m.list_bot_servers.return_value = [_entry()]
    m.get_bot_server.return_value = _entry()
    m.add_bot_server = AsyncMock(return_value={"server": _entry(), "changed": True})
    m.set_bot_server_active = AsyncMock(
        return_value={"server": _entry(active=True), "changed": True}
    )
    m.remove_bot_server = AsyncMock(return_value=True)
    return m


@pytest.fixture
def client(state):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotMcpStateServiceProtocol, to=state)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "u1")


def _ok(resp, code=200000, status=200):
    body = resp.json()
    assert resp.status_code == status, body
    assert body["code"] == code, body
    return body["data"]


# ── listing ─────────────────────────────────────────────────────────


def test_list_returns_servers_with_their_state(client, state):
    state.list_bot_servers.return_value = [
        _entry(active=True),
        _entry("mcp.builtin", active=True, is_default=True),
    ]
    data = _ok(client.get(BASE))
    assert data["total"] == 2
    assert data["items"][0]["server_code"] == CODE
    assert data["items"][1]["is_default"] is True


def test_list_pages(client, state):
    state.list_bot_servers.return_value = [_entry(f"mcp.s{i}") for i in range(5)]
    data = _ok(client.get(f"{BASE}?page=2&page_size=2"))
    assert data["total"] == 5
    assert [i["server_code"] for i in data["items"]] == ["mcp.s2", "mcp.s3"]


def test_list_of_a_bot_with_nothing_is_an_empty_page(client, state):
    state.list_bot_servers.return_value = []
    assert _ok(client.get(BASE)) == {"total": 0, "items": []}


def test_get_one_server(client):
    data = _ok(client.get(f"{BASE}/{CODE}"))
    assert data["server_code"] == CODE
    assert data["active"] is False


def test_a_server_not_on_the_bot_is_404(client, state):
    state.get_bot_server.side_effect = McpBotServerNotFoundError(CODE)
    resp = client.get(f"{BASE}/{CODE}")
    assert resp.status_code == 404
    assert resp.json()["message"] == "Not found"


def test_an_unowned_bot_answers_identically_to_an_unknown_one(client, state):
    """Neither is a way to learn whether a bot exists."""
    state.list_bot_servers.side_effect = McpBotServerNotFoundError("nope")
    unknown = client.get("/openapi/v1/bots/does-not-exist/mcp").json()
    state.list_bot_servers.side_effect = McpBotServerNotFoundError("b-2")
    unowned = client.get("/openapi/v1/bots/b-2/mcp").json()
    unknown.pop("request_id", None)
    unowned.pop("request_id", None)
    assert unknown == unowned


# ── add ─────────────────────────────────────────────────────────────


def test_add_answers_201_and_the_server_is_inactive(client):
    data = _ok(
        client.post(BASE, json={"server_code": CODE}), code=201000, status=201
    )
    assert data["changed"] is True
    assert data["server"]["active"] is False


def test_adding_an_existing_server_answers_200_not_201(client, state):
    # Nothing was created, so 201 would be a lie.
    state.add_bot_server = AsyncMock(
        return_value={"server": _entry(), "changed": False}
    )
    data = _ok(client.post(BASE, json={"server_code": CODE}))
    assert data["changed"] is False


def test_add_of_an_unknown_or_hidden_server_is_404(client, state):
    state.add_bot_server = AsyncMock(side_effect=McpServerNotFoundError(CODE))
    resp = client.post(BASE, json={"server_code": CODE})
    assert resp.status_code == 404


def test_add_with_an_unknown_body_field_is_422(client):
    resp = client.post(BASE, json={"server_code": CODE, "active": True})
    assert resp.status_code == 422


def test_add_with_an_empty_server_code_is_422(client):
    resp = client.post(BASE, json={"server_code": ""})
    assert resp.status_code == 422


def test_add_that_cannot_reconcile_the_runtime_is_502(client, state):
    state.add_bot_server = AsyncMock(side_effect=McpSyncFailedError("device down"))
    resp = client.post(BASE, json={"server_code": CODE})
    assert resp.status_code == 502


# ── activate / deactivate ───────────────────────────────────────────


def test_activate(client, state):
    data = _ok(client.post(f"{BASE}/{CODE}/activate"))
    assert data["server"]["active"] is True
    assert data["changed"] is True
    assert state.set_bot_server_active.call_args.kwargs["active"] is True


def test_deactivate(client, state):
    state.set_bot_server_active = AsyncMock(
        return_value={"server": _entry(active=False), "changed": True}
    )
    data = _ok(client.post(f"{BASE}/{CODE}/deactivate"))
    assert data["server"]["active"] is False
    assert state.set_bot_server_active.call_args.kwargs["active"] is False


def test_activating_an_already_active_server_is_success_with_changed_false(
    client, state
):
    state.set_bot_server_active = AsyncMock(
        return_value={"server": _entry(active=True), "changed": False}
    )
    data = _ok(client.post(f"{BASE}/{CODE}/activate"))
    assert data["changed"] is False


def test_activating_a_server_not_on_the_bot_is_404(client, state):
    state.set_bot_server_active = AsyncMock(
        side_effect=McpBotServerNotFoundError(CODE)
    )
    resp = client.post(f"{BASE}/{CODE}/activate")
    assert resp.status_code == 404


# ── remove ──────────────────────────────────────────────────────────


def test_remove(client):
    data = _ok(client.delete(f"{BASE}/{CODE}"))
    assert data == {"server_code": CODE, "removed": True}


def test_removing_a_server_the_bot_does_not_have_is_success_reporting_false(
    client, state
):
    state.remove_bot_server = AsyncMock(return_value=False)
    data = _ok(client.delete(f"{BASE}/{CODE}"))
    assert data["removed"] is False


def test_removing_an_engine_default_is_409_pointing_at_deactivate(client, state):
    state.remove_bot_server = AsyncMock(
        side_effect=McpDefaultServerNotRemovableError(CODE)
    )
    resp = client.delete(f"{BASE}/{CODE}")
    assert resp.status_code == 409
    assert "deactivate" in resp.json()["message"].lower()


# ── identity ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "", None),
        ("get", f"/{CODE}", None),
        ("post", "", {"server_code": CODE}),
        ("post", f"/{CODE}/activate", None),
        ("post", f"/{CODE}/deactivate", None),
        ("delete", f"/{CODE}", None),
    ],
)
def test_every_operation_refuses_a_user_id_naming_someone_else(
    client, method, path, body
):
    kwargs = {"params": {"user_id": "someone-else"}}
    if body is not None:
        kwargs["json"] = body
    resp = getattr(client, method)(f"{BASE}{path}", **kwargs)
    assert resp.status_code == 403, resp.json()


def test_every_operation_is_401_without_a_principal(client):
    def _no_caller():
        raise MissingPrincipalError("no verified caller for this request")

    client.app.dependency_overrides[require_principal] = _no_caller
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/{CODE}/activate").status_code == 401


# ── routing ─────────────────────────────────────────────────────────


def test_the_account_level_mcp_paths_still_resolve_to_their_own_group():
    """``/openapi/v1/bots/mcp/**`` must not be captured by ``{bot_id}``.

    The two groups differ on every path pair by segment count and literal, so
    this holds — but the near-miss is not obvious to someone adding a route
    later, and the failure would be a confusing 404 far from its cause.
    """
    from agentclaw.community.adapters.http.openapi_v1 import build_public_router

    def _api_routes(router) -> list:
        # ``include_router`` stores a lazy wrapper rather than copying routes,
        # so the nesting has to be walked. Same shape as ``_api_routes`` in
        # ``test_principal_seam.py``; duplicated rather than imported so this
        # file does not depend on another test module's internals.
        found = []
        for route in getattr(router, "routes", []):
            if hasattr(route, "dependant"):
                found.append(route)
            elif hasattr(route, "original_router"):
                found.extend(_api_routes(route.original_router))
            else:
                found.extend(_api_routes(route))
        return found

    by_path = {r.path for r in _api_routes(build_public_router())}

    for account_level in (
        "/openapi/v1/bots/mcp/servers",
        "/openapi/v1/bots/mcp/tenants",
        "/openapi/v1/bots/mcp/configs",
        "/openapi/v1/bots/mcp/servers/{server_code}",
        "/openapi/v1/bots/mcp/servers/{server_code}/config",
    ):
        assert account_level in by_path, account_level

    # And the bot-scoped group is genuinely mounted.
    assert "/openapi/v1/bots/{bot_id}/mcp" in by_path
    assert "/openapi/v1/bots/{bot_id}/mcp/{server_code}" in by_path
