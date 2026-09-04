"""HttpBcsChannelBindingClient wire-level tests (MockTransport, no real BCS)."""
import json
from datetime import UTC, datetime

import httpx
import pytest

from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,
    ChannelSyncError,
)
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.core.channel.services.bcs_binding_client import (
    HttpBcsChannelBindingClient,
)

_BINDING_PATH = "/openapi/v1/collaboration/channels/bindings"


def _record(**config_extra) -> ChannelRecord:
    config = {
        "client_id": "client-1",
        "client_secret": "secret-1",
        "robot_code": "robot-1",
        "binding_mode": "bcn_gateway",
    }
    config.update(config_extra)
    return ChannelRecord(
        id=7,
        type="dingding",
        description=None,
        identity_id="user-1",
        bind_bot_id="bot-1",
        config=config,
        status="1",
        deleted=0,
        gmt_create=datetime(2026, 9, 3, tzinfo=UTC),
        gmt_modified=datetime(2026, 9, 3, tzinfo=UTC),
        env="dev",
        stage="draft",
    )


def _client(handler) -> HttpBcsChannelBindingClient:
    return HttpBcsChannelBindingClient(
        base_url="http://bcs.test",
        service_token="token-1",
        transport=httpx.MockTransport(handler),
    )


def _ok(data) -> dict:
    return {"code": 20100, "message": "Created", "data": data, "request_id": "r-1"}


@pytest.mark.asyncio
async def test_ensure_active_posts_binding_and_returns_id():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer token-1"
        body = json.loads(request.content)
        assert body["channel_type"] == "dingtalk"
        assert body["account_ref"] == "client-1"
        assert body["target"] == {"bot": {"bot_id": "bot-1"}}
        assert body["group_chat_scope"] == "per_sender"
        assert body["outbound_visibility"] == "full_transcript"
        assert body["config"]["robot_code"] == "robot-1"
        assert body["config"]["send_mode"] == {"mode": "normal", "message_type": "markdown"}
        return httpx.Response(201, json=_ok({"id": "bcs-1"}))

    assert await _client(handler).ensure_active(_record()) == "bcs-1"
    assert ("POST", _BINDING_PATH) in seen


@pytest.mark.asyncio
async def test_ensure_active_maps_streaming_card_mode():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["config"]["send_mode"] == {
            "mode": "streaming_card",
            "card_template_id": "card-1",
            "fallback_message_type": "markdown",
        }
        return httpx.Response(201, json=_ok({"id": "bcs-1"}))

    record = _record(enable_streaming_cards=True, card_template_id="card-1")
    await _client(handler).ensure_active(record)


@pytest.mark.asyncio
async def test_ensure_active_with_stored_id_patches_active():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "PATCH":
            assert json.loads(request.content) == {"active": True}
            return httpx.Response(200, json=_ok({"id": "bcs-1"}))
        return httpx.Response(201, json=_ok({"id": "unexpected"}))

    record = _record(bcs_binding_id="bcs-1")
    assert await _client(handler).ensure_active(record) == "bcs-1"
    assert ("PATCH", f"{_BINDING_PATH}/bcs-1") in seen
    assert not any(m == "POST" for m, _ in seen)


@pytest.mark.asyncio
async def test_ensure_active_recovers_id_via_by_target_on_conflict():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(409, json={"code": 40900, "message": "conflict"})
        if request.url.path.endswith("/by-target"):
            assert "target_type=bot" in str(request.url)
            assert "target_id=bot-1" in str(request.url)
            return httpx.Response(200, json=_ok({
                "items": [{"id": "bcs-9", "account_ref": "client-1"}]
            }))
        return httpx.Response(200, json=_ok({"id": "bcs-9"}))

    assert await _client(handler).ensure_active(_record()) == "bcs-9"
    assert ("PATCH", f"{_BINDING_PATH}/bcs-9") in seen


@pytest.mark.asyncio
async def test_ensure_active_conflict_without_recovery_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"code": 40900, "message": "conflict"})
        return httpx.Response(200, json=_ok({"items": []}))

    with pytest.raises(ChannelBindingConflictError):
        await _client(handler).ensure_active(_record())


@pytest.mark.asyncio
async def test_push_config_patches_full_config():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        body = json.loads(request.content)
        assert set(body) == {"config"}
        assert body["config"]["client_id"] == "client-1"
        return httpx.Response(200, json=_ok(None))

    await _client(handler).push_config(_record(), binding_id="bcs-1")


@pytest.mark.asyncio
async def test_unconfigured_base_url_raises_sync_error():
    client = HttpBcsChannelBindingClient(base_url="", service_token="")
    with pytest.raises(ChannelSyncError):
        await client.ensure_active(_record())


@pytest.mark.asyncio
async def test_network_error_raises_sync_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ChannelSyncError):
        await _client(handler).ensure_active(_record())
