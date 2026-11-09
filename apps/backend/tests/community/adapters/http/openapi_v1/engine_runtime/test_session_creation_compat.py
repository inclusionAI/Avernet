"""Mixed-version OpenClaw creation keeps successful writes successful."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import (
    converter_creation,
    router,
)
from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.core.engine_runtime.models import EngineResult
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTimeoutError

from .conftest import BOT, OWNER

PATH = f"/openapi/v1/bots/{BOT}/sessions"
SESSION_ID = f"session:abc-123:user:{OWNER}"
CANONICAL_ID = f"agent:main:{SESSION_ID}"
CREATED = {
    "id": SESSION_ID,
    "title": "Legacy title",
    "model": None,
    "message_count": 0,
    "gmt_created": "2026-11-05T09:00:00+00:00",
    "gmt_modified": "2026-11-05T09:00:00+00:00",
}
LISTED = {
    **CREATED,
    "id": CANONICAL_ID,
    "title": "Listed title",
    "model": "provider/resolved-model",
    "gmt_created": "2026-11-05T09:00:01+00:00",
    "gmt_modified": "2026-11-05T09:00:02+00:00",
}


@pytest.fixture
def client(make_client):
    return make_client(router)


@pytest.fixture(autouse=True)
def sleep(monkeypatch):
    stub = AsyncMock()
    monkeypatch.setattr(converter_creation, "asyncio", SimpleNamespace(sleep=stub))
    return stub


@pytest.mark.parametrize("title", [None, "", "Requested title"])
def test_legacy_result_is_reconciled_without_recreating(client, relay, sleep, title):
    relay.results = [EngineResult(data=CREATED), EngineResult(data=[LISTED])]

    response = client.post(PATH, json={} if title is None else {"title": title})

    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert data["session_id"] == CANONICAL_ID
    assert data["model"] == LISTED["model"]
    assert data["title"] == (LISTED["title"] if title is None else title)
    assert data["gmt_create"] == CREATED["gmt_created"]
    assert data["gmt_modified"] == CREATED["gmt_modified"]
    assert [c["method"] for c in relay.calls] == ["POST", "GET"]
    assert relay.paths == ["/api/sessions", "/api/sessions"]
    assert relay.calls[1]["params"] == {"offset": 0, "limit": 20, "user_id": OWNER}
    sleep.assert_not_awaited()


@pytest.mark.parametrize("ext_info", [
    None, [], "invalid", {}, {"other": "legacy"},
    {"openclaw_creation": None}, {"openclaw_creation": []},
    {"openclaw_creation": {}},
    {"openclaw_creation": {"model": "p/m"}},
    {"openclaw_creation": {"session_id": 123, "model": "p/m"}},
    {"openclaw_creation": {"session_id": " ", "model": "p/m"}},
    {"openclaw_creation": {"session_id": CANONICAL_ID}},
    {"openclaw_creation": {"session_id": CANONICAL_ID, "model": None}},
    {"openclaw_creation": {"session_id": CANONICAL_ID, "model": 123}},
    {"openclaw_creation": {"session_id": CANONICAL_ID, "model": " "}},
])
def test_missing_or_incomplete_metadata_uses_legacy_readback(
    client, relay, monkeypatch, ext_info
):
    logger = Mock()
    monkeypatch.setattr(converter_creation, "logger", logger)
    relay.results = [
        EngineResult(data={**CREATED, "ext_info": ext_info}),
        EngineResult(data=[LISTED]),
    ]

    response = client.post(PATH, json={"title": "T"})

    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert data["session_id"] == CANONICAL_ID
    assert data["model"] == LISTED["model"]
    assert "ext_info" not in data
    assert [c["method"] for c in relay.calls] == ["POST", "GET"]
    if isinstance(ext_info, dict) and "openclaw_creation" in ext_info:
        logger.warning.assert_called_once_with(
            "[openapi.sessions.create] invalid creation metadata; using legacy reconciliation"
        )
    else:
        logger.warning.assert_not_called()


def test_complete_metadata_does_not_read_or_sleep(client, relay, sleep):
    relay.results = [EngineResult(data={
        **CREATED,
        "ext_info": {"openclaw_creation": {
            "session_id": CANONICAL_ID, "model": LISTED["model"],
        }},
    })]

    response = client.post(PATH, json={"title": "T"})

    assert response.status_code == 201, response.json()
    assert response.json()["data"]["session_id"] == CANONICAL_ID
    assert [c["method"] for c in relay.calls] == ["POST"]
    sleep.assert_not_awaited()


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("caller", [OWNER, "collaborator"])
def test_readback_keeps_the_verified_caller_stage_and_instance(
    make_client, relay, stage, caller
):
    relay.set_bot_type("service")
    relay.add_operator(caller)
    client = make_client(router, caller=caller)
    relay.results = [EngineResult(data=CREATED), EngineResult(data=[LISTED])]

    response = client.post(PATH, json={}, params={
        "owner_id": OWNER, "stage": stage, "device_uuid": "instance-B",
    })

    assert response.status_code == 201, response.json()
    assert len(relay.calls) == 2
    for forwarded in relay.calls:
        assert forwarded["bot_id"] == BOT
        assert forwarded["owner_id"] == OWNER
        assert forwarded["stage"] == stage
        assert forwarded["device_uuid"] == "instance-B"
    assert relay.calls[0]["body"]["user_id"] == caller
    assert relay.calls[1]["params"]["user_id"] == caller


@pytest.mark.parametrize("attempt", [2, 3])
def test_delayed_visibility_is_retried_with_the_original_waits(client, relay, sleep, attempt):
    relay.results = [EngineResult(data=CREATED)] + [
        EngineResult(data=[]) for _ in range(attempt - 1)
    ] + [EngineResult(data={"items": [LISTED]})]

    response = client.post(PATH, json={})

    assert response.status_code == 201, response.json()
    assert response.json()["data"]["session_id"] == CANONICAL_ID
    assert [c["method"] for c in relay.calls] == ["POST"] + ["GET"] * attempt
    assert sleep.await_args_list == [call(0.5), call(1.5)][:attempt - 1]


@pytest.mark.parametrize("listed", [[], None, {"items": []}, {"items": 42}, [
    None, "not a row", {**LISTED, "id": "agent:main:session:other:user:u1"},
]])
def test_exhausted_or_malformed_readback_returns_original_success(client, relay, sleep, listed):
    relay.results = [EngineResult(data=CREATED), EngineResult(data=listed)]

    response = client.post(PATH, json={})

    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert data["session_id"] == SESSION_ID
    assert data["model"] == ""
    assert data["title"] == CREATED["title"]
    assert data["gmt_create"] == CREATED["gmt_created"]
    assert data["gmt_modified"] == CREATED["gmt_modified"]
    assert [c["method"] for c in relay.calls] == ["POST", "GET", "GET", "GET"]
    assert sleep.await_args_list == [call(0.5), call(1.5)]


@pytest.mark.parametrize("error", [
    EngineUpstreamError("list failed"), DeviceAdapterTimeoutError("list timed out"),
])
@pytest.mark.parametrize("recovers", [True, False])
def test_readback_error_never_retries_the_write(client, relay, sleep, error, recovers):
    outcomes = [EngineResult(data=CREATED), error]
    outcomes += [EngineResult(data=[LISTED])] if recovers else [error, error]
    relay.call = AsyncMock(side_effect=outcomes)

    response = client.post(PATH, json={})

    assert response.status_code == 201, response.json()
    assert response.json()["data"]["session_id"] == (CANONICAL_ID if recovers else SESSION_ID)
    assert [c.kwargs["method"] for c in relay.call.await_args_list] == (
        ["POST", "GET", "GET"] if recovers else ["POST", "GET", "GET", "GET"]
    )
    assert sleep.await_args_list == ([call(0.5)] if recovers else [call(0.5), call(1.5)])


@pytest.mark.parametrize("error,status", [
    (EngineUpstreamError("create failed"), 502),
    (DeviceAdapterTimeoutError("create timed out"), 504),
])
def test_failed_creation_is_not_hidden_or_retried(client, relay, sleep, error, status):
    relay.raises = error

    response = client.post(PATH, json={})

    assert response.status_code == status, response.json()
    assert [c["method"] for c in relay.calls] == ["POST"]
    sleep.assert_not_awaited()


@pytest.mark.parametrize("listed_model,expected", [(None, "requested"), ("", "requested"), ("p/m", "p/m")])
def test_readback_preserves_the_created_model_when_missing_from_list(client, relay, listed_model, expected):
    relay.results = [
        EngineResult(data={**CREATED, "model": "requested"}),
        EngineResult(data=[{**LISTED, "model": listed_model}]),
    ]

    response = client.post(PATH, json={"model": "requested"})

    assert response.status_code == 201, response.json()
    assert response.json()["data"]["model"] == expected


def test_list_timestamps_are_kept_when_creation_does_not_supply_them(client, relay):
    relay.results = [
        EngineResult(data={**CREATED, "gmt_created": None, "gmt_modified": ""}),
        EngineResult(data=[LISTED]),
    ]

    response = client.post(PATH, json={})

    assert response.status_code == 201, response.json()
    assert response.json()["data"]["gmt_create"] == LISTED["gmt_created"]
    assert response.json()["data"]["gmt_modified"] == LISTED["gmt_modified"]


@pytest.mark.parametrize("agent", ["main", "custom-agent"])
def test_readback_recognizes_lowercased_managed_keys_without_guessing_agent(client, relay, agent):
    canonical = f"agent:{agent}:session:abc-123:user:clouduser"
    relay.results = [
        EngineResult(data={**CREATED, "id": "session:ABC-123:user:CloudUser"}),
        EngineResult(data=[{**LISTED, "id": canonical}]),
    ]

    response = client.post(PATH, json={})

    assert response.status_code == 201, response.json()
    assert response.json()["data"]["session_id"] == canonical


@pytest.mark.parametrize("candidate,created,expected", [
    ("Opaque-ID", "Opaque-ID", True),
    ("opaque-id", "Opaque-ID", False),
    (CANONICAL_ID, "opaque-id", False),
    (CANONICAL_ID, SESSION_ID, True),
    (SESSION_ID, CANONICAL_ID, True),
    (CANONICAL_ID, f"agent:other:{SESSION_ID}", False),
    (SESSION_ID.lower(), SESSION_ID.upper(), False),
    (CANONICAL_ID, "session:other:user:u1", False),
])
def test_reconciliation_retains_legacy_matching_rules(candidate, created, expected):
    assert converter_creation._matches(candidate, created) is expected


@pytest.mark.parametrize("session_id", [None, ""])
def test_missing_legacy_id_does_not_trigger_unmatchable_readbacks(client, relay, sleep, session_id):
    relay.results = [EngineResult(data={**CREATED, "id": session_id})]

    response = client.post(PATH, json={})

    assert response.status_code == 201, response.json()
    assert [c["method"] for c in relay.calls] == ["POST"]
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_cancellation_is_not_swallowed(relay, sleep):
    relay.call = AsyncMock(side_effect=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await converter_creation.reconcile_created_session(
            relay=relay, facts=relay.bots[(BOT, OWNER)], bot_id=BOT,
            owner_id=OWNER, user_id=OWNER, stage=RuntimeStage.DRAFT,
            created_item=CREATED, requested_title=None,
        )

    relay.call.assert_awaited_once()
    sleep.assert_not_awaited()
