"""Tests for the user-level Agent Coding work-items aggregation endpoint."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.api.work_items_service import (
    AICodingWorkItemsServiceProtocol,
)
from agentclaw.community.adapters.http.aicoding.work_items_router import router
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.aicoding.work_item_bots import (
    WorkItemsBotLister,
    WorkItemsBotTarget,
)
from agentclaw.community.core.aicoding.services import work_items_service
from agentclaw.community.core.aicoding.services.work_items_service import (
    AICodingWorkItemsService,
)
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTransport,
)


@pytest.fixture
def mock_user():
    user = MagicMock(spec=AuthenticatedIdentity)
    user.staffId = "315629"
    return user


def _target(bot_id="bot-1", bot_name="Bot 1", owner_id="315629"):
    return WorkItemsBotTarget(bot_id=bot_id, bot_name=bot_name, owner_id=owner_id)


def _ctx(provider="baas", conn_info=None):
    context = MagicMock()
    context.provider = provider
    context.conn_info = conn_info or {"type": "baas"}
    return context


def _resolver(context=None, side_effect=None):
    resolver = MagicMock(spec=DeviceContextResolver)
    if side_effect is not None:
        resolver.resolve_for_bot.side_effect = side_effect
    else:
        resolver.resolve_for_bot.return_value = context or _ctx()
    return resolver


def _transport(response=None, side_effect=None):
    transport = MagicMock(spec=DeviceAdapterTransport)
    if side_effect is not None:
        transport.invoke = AsyncMock(side_effect=side_effect)
    else:
        transport.invoke = AsyncMock(
            return_value=response
            if response is not None
            else {"success": True, "data": []}
        )
    return transport


def _client(test_injector, mock_user, bot_lister, resolver, transport):
    app = FastAPI()
    app.include_router(router)

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(DeviceAdapterTransport, to=transport)
            binder.bind(
                AICodingWorkItemsServiceProtocol,
                to=AICodingWorkItemsService(bot_lister, resolver, transport),
            )

    injector = Injector([_TestModule()], parent=test_injector)
    attach_injector(app, injector)
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return TestClient(app)


def _session(session_id, updated_at, run_status, title=None, user_id="315629"):
    return {
        "id": session_id,
        "title": title or session_id,
        "user_id": user_id,
        "gmt_modified": updated_at,
        "cwd": "/workspace/app",
        "model": "GLM-5.2",
        "ext_info": {"dimaItemId": "dima-1"},
        "run_status": run_status,
    }


def test_work_items_passes_the_query_to_relay_and_applies_fallback_filters(
    test_injector, mock_user
):
    sessions = [
        _session("user:315629:session:a1:agent:bot-1", "1794000000000Z", "running"),
        _session(
            "user:999999:session:a2:agent:bot-1",
            "1794000001000Z",
            "awaitingHuman",
        ),
        _session("user:315629:session:a3:agent:bot-1", "1794000002000Z", "idle"),
        _session(
            "user:315629:session:a4:agent:bot-1", "1794000003000Z", "futureStatus"
        ),
    ]
    lister = MagicMock(spec=WorkItemsBotLister)
    lister.list_bot_targets.return_value = [_target()]
    resolver = _resolver(_ctx(provider="baas"))
    transport = _transport({"success": True, "data": sessions})
    client = _client(test_injector, mock_user, lister, resolver, transport)

    response = client.get(
        "/api/v1/aicoding/work-items",
        params={
            "source": "mine",
            "since_ms": 1_794_000_000_000,
            "run_status": "non_idle",
            "limit": 50,
        },
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert [item["session_key"] for item in payload["items"]] == [
        "user:315629:session:a4:agent:bot-1",
        "user:315629:session:a1:agent:bot-1",
    ]
    assert payload["items"][0]["run_status"] == "futureStatus"
    assert payload["items"][0]["source"] == "mine"
    assert payload["items"][0]["updated_at_ms"] == 1_794_000_003_000
    assert payload["bots"][0]["count"] == 2
    assert payload["bots"][0]["status"] == "ok"
    assert payload["scan_limit"] == 50
    assert payload["filters"] == {
        "source": ["mine"],
        "since_ms": 1_794_000_000_000,
        "run_status": ["non_idle"],
    }

    _, kwargs = transport.invoke.call_args
    assert kwargs["params"] == {
        "user_id": "315629",
        "session_kind": "user",
        "limit": 50,
        "offset": 0,
        "source": "mine",
        "since_ms": 1_794_000_000_000,
        "run_status": "non_idle",
    }
    assert "agent_id" not in kwargs["params"]


def test_work_items_defaults_are_all_optional(test_injector, mock_user):
    lister = MagicMock(spec=WorkItemsBotLister)
    lister.list_bot_targets.return_value = [_target()]
    resolver = _resolver()
    transport = _transport(
        {
            "success": True,
            "data": [_session("user:315629:s", "1794000000000Z", "running")],
        }
    )
    client = _client(test_injector, mock_user, lister, resolver, transport)

    response = client.get("/api/v1/aicoding/work-items")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["filters"] == {"source": None, "since_ms": None, "run_status": None}
    assert payload["scan_limit"] == 50
    assert len(payload["items"]) == 1
    _, kwargs = transport.invoke.call_args
    assert set(kwargs["params"]) == {"user_id", "session_kind", "limit", "offset"}


def test_partial_bot_failure_does_not_fail_work_items_or_successful_bots(
    test_injector, mock_user
):
    targets = [_target("ok-bot", "OK Bot"), _target("down-bot", "Down Bot")]
    lister = MagicMock(spec=WorkItemsBotLister)
    lister.list_bot_targets.return_value = targets

    def resolve(bot_id, owner_id):
        if bot_id == "down-bot":
            from agentclaw.community.core.devices.services.device_context import (
                DeviceNotBoundError,
            )

            raise DeviceNotBoundError("no active binding")
        return _ctx()

    resolver = _resolver(side_effect=resolve)
    transport = _transport(
        {
            "success": True,
            "data": [_session("user:315629:ok", "1794000000000Z", "running")],
        }
    )
    client = _client(test_injector, mock_user, lister, resolver, transport)

    response = client.get("/api/v1/aicoding/work-items")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["partial"] is False
    summaries = {summary["bot_id"]: summary for summary in payload["bots"]}
    assert summaries["ok-bot"]["status"] == "ok"
    assert summaries["ok-bot"]["count"] == 1
    assert summaries["down-bot"]["status"] == "unavailable"
    assert summaries["down-bot"]["count"] == 0


def test_engine_error_is_partial_and_keeps_other_bot_items(test_injector, mock_user):
    targets = [_target("ok-bot", "OK Bot"), _target("bad-bot", "Bad Bot")]
    lister = MagicMock(spec=WorkItemsBotLister)
    lister.list_bot_targets.return_value = targets

    def resolve(bot_id, owner_id):
        if bot_id == "bad-bot":
            return _ctx(conn_info={"type": "bad"})
        return _ctx()

    async def invoke(conn_info, method, path, params=None, timeout=None):
        if conn_info["type"] == "bad":
            raise DeviceAdapterHTTPStatusError(
                500, "secret_token=secret Authorization: Bearer abc"
            )
        return {
            "success": True,
            "data": [_session("user:315629:ok", "1794000000000Z", "running")],
        }

    resolver = _resolver(side_effect=resolve)
    transport = _transport(side_effect=invoke)
    client = _client(test_injector, mock_user, lister, resolver, transport)

    response = client.get("/api/v1/aicoding/work-items")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["partial"] is True
    assert len(payload["items"]) == 1
    summaries = {summary["bot_id"]: summary for summary in payload["bots"]}
    assert summaries["bad-bot"]["status"] == "error"
    assert summaries["bad-bot"]["error_code"] == "ENGINE_HTTP_500"
    assert summaries["bad-bot"]["error_message"] == (
        "Engine session list returned HTTP 500"
    )
    assert summaries["ok-bot"]["status"] == "ok"
    assert "secret_token" not in response.text
    assert "Bearer abc" not in response.text


def test_relay_error_details_are_not_exposed(test_injector, mock_user):
    lister = MagicMock(spec=WorkItemsBotLister)
    lister.list_bot_targets.return_value = [_target()]
    resolver = _resolver()
    transport = _transport(
        {
            "success": False,
            "message": "SESSION_LIST_TIMEOUT: secret_token=secret",
        }
    )
    client = _client(test_injector, mock_user, lister, resolver, transport)

    response = client.get("/api/v1/aicoding/work-items")

    assert response.status_code == 200
    summary = response.json()["data"]["bots"][0]
    assert summary["status"] == "error"
    assert summary["error_code"] == "SESSION_LIST_TIMEOUT"
    assert summary["error_message"] == "Engine session list failed"
    assert "secret_token" not in response.text


def test_malformed_engine_response_is_an_isolated_bot_error(test_injector, mock_user):
    lister = MagicMock(spec=WorkItemsBotLister)
    lister.list_bot_targets.return_value = [_target()]
    resolver = _resolver()
    transport = _transport(["not-an-envelope"])
    client = _client(test_injector, mock_user, lister, resolver, transport)

    response = client.get("/api/v1/aicoding/work-items")

    assert response.status_code == 200
    summary = response.json()["data"]["bots"][0]
    assert summary["status"] == "error"
    assert summary["error_code"] == "ENGINE_MALFORMED_RESPONSE"
    assert summary["error_message"] == (
        "Engine session list returned an invalid response"
    )


@pytest.mark.asyncio
async def test_local_probe_error_text_is_not_returned(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            raise RuntimeError("secret_token=secret Authorization: Bearer abc")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(work_items_service.httpx, "AsyncClient", FailingClient)

    result = await work_items_service._probe_local({})

    assert result == {"success": False, "message": "ENGINE_PROBE_ERROR"}


def test_fanout_remains_bounded_and_concurrent(test_injector, mock_user):
    targets = [_target(f"bot-{index}", f"Bot {index}") for index in range(20)]
    lister = MagicMock(spec=WorkItemsBotLister)
    lister.list_bot_targets.return_value = targets

    active = 0
    max_active = 0

    async def invoke(conn_info, method, path, params=None, timeout=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return {"success": True, "data": []}

    resolver = _resolver()
    transport = _transport(side_effect=invoke)
    client = _client(test_injector, mock_user, lister, resolver, transport)

    response = client.get("/api/v1/aicoding/work-items")

    assert response.status_code == 200
    assert len(response.json()["data"]["bots"]) == len(targets)
    assert max_active <= 12
    assert max_active > 1


def test_invalid_source_token_is_rejected(test_injector, mock_user):
    lister = MagicMock(spec=WorkItemsBotLister)
    client = _client(test_injector, mock_user, lister, _resolver(), _transport())

    response = client.get("/api/v1/aicoding/work-items", params={"source": "mine,all"})

    assert response.status_code == 400
    lister.list_bot_targets.assert_not_called()


@pytest.mark.parametrize(
    "params",
    [
        {"since_ms": "not-a-number"},
        {"since_ms": "0"},
        {"since_ms": "-1"},
        {"limit": "not-a-number"},
        {"limit": "0"},
        {"limit": "-1"},
        {"limit": "201"},
    ],
)
def test_invalid_numeric_query_is_rejected_with_400(test_injector, mock_user, params):
    lister = MagicMock(spec=WorkItemsBotLister)
    client = _client(test_injector, mock_user, lister, _resolver(), _transport())

    response = client.get("/api/v1/aicoding/work-items", params=params)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_QUERY"
    lister.list_bot_targets.assert_not_called()
