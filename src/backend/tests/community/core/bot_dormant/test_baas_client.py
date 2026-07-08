"""Unit tests for BaasDormantClient (single-signal /alive only).

Tests use the **real BaaS response envelope** verified against
``src/baas/src/secbaas/adapters/web/bot_health_checker_router.py`` and
``api/health_check/bot/_models.py:BotAliveCheckResult``:

    {
      "code": 0, "message": "success",
      "data": {
        "overall_alive": bool,
        "devices": [{"last_session_time": "...", ...}, ...]
      }
    }

Do **not** invent your own field names here — earlier versions of this client
parsed a non-existent ``data['result']`` key and silently returned 'unknown'
in production. Tests must speak the same dialect as the real endpoint.

The client now delegates transport to an injected ``HttpClient`` (BAAS-qualified).
Tests stub the HttpClient.get with MagicMock, exactly like BaasService tests.
"""
from __future__ import annotations

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from agentclaw.community.core.bot_dormant.baas_client import (
    AliveResult,
    BaasDormantClient,
)
from agentclaw.community.plugin_api.http_client import HttpClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client() -> tuple[BaasDormantClient, MagicMock]:
    """Construct a client with a mock HttpClient. Returns (client, http_mock)."""
    http = MagicMock(spec=HttpClient)
    return BaasDormantClient(http_client=http), http


def _envelope(
    *,
    overall_alive: bool,
    devices: list[dict] | None = None,
    code: int = 0,
    message: str = "success",
) -> dict:
    """Build a realistic ApiResponse envelope for /alive."""
    return {
        "code": code,
        "message": message,
        "data": {
            "bot_id": "bot1",
            "overall_alive": overall_alive,
            "alive_count": 1 if overall_alive else 0,
            "idle_count": 0 if overall_alive else 1,
            "unsupported_count": 0,
            "devices": devices or [],
        },
    }


def _ok_response(json_payload: dict) -> MagicMock:
    """Build a 200 OK Response-like mock returning the given JSON."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_payload
    return resp


# ---------------------------------------------------------------------------
# Happy path — overall_alive=true / false
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_overall_true_no_devices():
    """overall_alive=True with empty devices → AliveResult('true', None)."""
    client, http = _make_client()
    http.get.return_value = _ok_response(_envelope(overall_alive=True))

    result = await client.check_alive(bot_id="bot1", entity_id="e1", minutes=30)

    assert result == AliveResult(result="true", last_session_time=None)
    # Verify path + params shape — relative path, no host
    call = http.get.call_args
    assert call.args[0] == "/internal/bot-health-checker/alive"
    assert call.kwargs["params"] == {
        "bot_id": "bot1", "entity_id": "e1", "minutes": 30,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_overall_false_picks_max_session_time():
    """overall_alive=False + multiple devices → result='false' + MAX last_session_time."""
    client, http = _make_client()
    devices = [
        {"paas_device_id": "d1", "alive": False, "status": "idle",
         "last_session_time": "2026-06-10 10:00:00"},
        {"paas_device_id": "d2", "alive": False, "status": "idle",
         "last_session_time": "2026-06-12 15:30:00"},  # latest
        {"paas_device_id": "d3", "alive": False, "status": "idle",
         "last_session_time": None},
    ]
    http.get.return_value = _ok_response(
        _envelope(overall_alive=False, devices=devices)
    )

    result = await client.check_alive(bot_id="bot1", entity_id="e1", minutes=30)

    assert result == AliveResult(
        result="false", last_session_time="2026-06-12 15:30:00"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_sends_no_authorization_header():
    """The /internal/bot-health-checker/alive endpoint is MOSN-secured and
    does not take API-key auth. The client must NOT attach Authorization
    (limo, 2026-06-26: switched from /api/v1/... external to /internal/...
    after the external endpoint required a health-checker API key we
    couldn't easily provision)."""
    client, http = _make_client()
    http.get.return_value = _ok_response(_envelope(overall_alive=True))

    await client.check_alive(bot_id="bot1", entity_id="e1", minutes=30)

    # No headers kwarg passed at all (or passed without Authorization).
    headers = http.get.call_args.kwargs.get("headers", {})
    assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# Envelope defensiveness — malformed responses → unknown (NEVER misclassify)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_non_zero_code_returns_unknown():
    """BaaS returned error code (non-zero) → unknown, not crash."""
    client, http = _make_client()
    http.get.return_value = _ok_response(
        {"code": 500, "message": "internal error", "data": None}
    )

    result = await client.check_alive(bot_id="bot1", entity_id="e1", minutes=30)

    assert result == AliveResult(result="unknown", last_session_time=None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_missing_data_returns_unknown():
    """Envelope without `data` field → unknown."""
    client, http = _make_client()
    http.get.return_value = _ok_response({"code": 0, "message": "ok"})

    result = await client.check_alive(bot_id="bot1", entity_id="e1", minutes=30)

    assert result.result == "unknown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_overall_alive_not_bool_returns_unknown():
    """If overall_alive is missing or non-bool (schema drift) → unknown."""
    client, http = _make_client()
    envelope = _envelope(overall_alive=True)
    envelope["data"]["overall_alive"] = "yes"  # bad type
    http.get.return_value = _ok_response(envelope)

    result = await client.check_alive(bot_id="bot1", entity_id="e1", minutes=30)

    assert result.result == "unknown"


# ---------------------------------------------------------------------------
# Retry behaviour — transport / HTTP errors
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_all_retries_fail_returns_unknown():
    """3 consecutive transport errors → AliveResult(result='unknown')."""
    client, http = _make_client()
    http.get.side_effect = httpx.ConnectError("connection refused")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await client.check_alive(
            bot_id="bot_fail", entity_id="e_fail", minutes=30
        )

    assert result == AliveResult(result="unknown", last_session_time=None)
    assert http.get.call_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_alive_http_error_returns_unknown():
    """HTTP 401 (auth) on all retries → AliveResult(result='unknown'),
    proves we don't silently classify all bots as dormant when the API
    key is wrong."""
    client, http = _make_client()
    err_resp = MagicMock()
    err_resp.status_code = 401
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "unauthorized", request=MagicMock(), response=err_resp,
    )
    http.get.return_value = err_resp

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await client.check_alive(
            bot_id="bot_401", entity_id="e", minutes=30
        )

    assert result.result == "unknown"


# Constructor takes only http_client (HttpClient injected by DI). No env
# vars, no api_key — those were dropped when the client switched from the
# external /api/v1/... endpoint to the internal /internal/... one.
