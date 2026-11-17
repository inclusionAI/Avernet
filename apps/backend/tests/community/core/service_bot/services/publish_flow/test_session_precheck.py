"""SessionPrechecker: active-session check before an online upgrade."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError
from agentclaw.community.core.engine_runtime.models import EngineResult
from agentclaw.community.core.service_bot.services.publish_flow.session_precheck import (
    SESSION_PRECHECK_LIMIT,
    SessionPrechecker,
)


def _bot(bot_type: str = "service") -> dict:
    return {"bot_id": "b1", "owner_id": "u1", "bot_type": bot_type}


def _relay(data: object) -> MagicMock:
    relay = MagicMock()
    relay.call = AsyncMock(return_value=EngineResult(data=data))
    return relay


@pytest.mark.asyncio
async def test_service_bot_with_active_sessions_returns_count() -> None:
    relay = _relay([{"session_id": "s1"}, {"session_id": "s2"}])

    count = await SessionPrechecker(relay=relay).active_session_count(_bot())

    assert count == 2
    kwargs = relay.call.await_args.kwargs
    assert kwargs["bot_id"] == "b1"
    assert kwargs["owner_id"] == "u1"
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == "/api/sessions"
    assert kwargs["params"] == {"limit": SESSION_PRECHECK_LIMIT, "offset": 0}
    assert kwargs["stage"] == "online"


@pytest.mark.asyncio
async def test_service_bot_with_no_sessions_returns_zero() -> None:
    relay = _relay([])

    count = await SessionPrechecker(relay=relay).active_session_count(_bot())

    assert count == 0


@pytest.mark.asyncio
async def test_relay_error_returns_zero() -> None:
    relay = MagicMock()
    relay.call = AsyncMock(side_effect=EngineDeviceNotReadyError("no device"))

    count = await SessionPrechecker(relay=relay).active_session_count(_bot())

    assert count == 0


@pytest.mark.asyncio
async def test_personal_bot_skips_check() -> None:
    relay = _relay([{"session_id": "s1"}])

    count = await SessionPrechecker(relay=relay).active_session_count(_bot("personal"))

    assert count == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_without_id_is_not_checked() -> None:
    relay = _relay([{"session_id": "s1"}])

    count = await SessionPrechecker(relay=relay).active_session_count({"owner_id": "u1"})

    assert count == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_without_owner_is_not_checked() -> None:
    relay = _relay([{"session_id": "s1"}])

    count = await SessionPrechecker(relay=relay).active_session_count({"bot_id": "b1"})

    assert count == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_relay_is_cut_off_by_timeout() -> None:
    async def _hang(**_kwargs: object) -> EngineResult:
        await asyncio.sleep(5)
        return EngineResult(data=[])

    relay = MagicMock()
    relay.call = _hang

    checker = SessionPrechecker(relay=relay, timeout_s=0.05)
    count = await checker.active_session_count(_bot())

    assert count == 0


@pytest.mark.asyncio
async def test_non_list_data_returns_zero() -> None:
    relay = _relay({"sessions": [{"id": "s1"}]})

    count = await SessionPrechecker(relay=relay).active_session_count(_bot())

    assert count == 0
