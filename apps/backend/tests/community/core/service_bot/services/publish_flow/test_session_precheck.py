"""SessionPrechecker: block online publish only while runs are in flight."""

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


def _relay(rows: object) -> MagicMock:
    relay = MagicMock()
    relay.call = AsyncMock(return_value=EngineResult(data=rows))
    return relay


@pytest.mark.asyncio
async def test_service_bot_queries_aicoding_sessions_route_as_owner() -> None:
    relay = _relay([{"run_status": "running"}])

    count = await SessionPrechecker(relay=relay).active_session_count(_bot())

    assert count == 1
    kwargs = relay.call.await_args.kwargs
    assert kwargs["bot_id"] == "b1"
    assert kwargs["owner_id"] == "u1"
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == "/api/aicoding/sessions"
    assert kwargs["params"] == {"limit": SESSION_PRECHECK_LIMIT, "offset": 0}
    assert kwargs["stage"] == "online"


@pytest.mark.asyncio
async def test_running_run_counts_as_active() -> None:
    relay = _relay([{"run_status": "running"}, {"run_status": "running"}])

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 2


@pytest.mark.asyncio
async def test_awaiting_kinds_count_as_in_flight() -> None:
    """Approval / human / LLM-evaluation waits hold work in flight too."""
    relay = _relay(
        [
            {"run_status": "awaitingApproval"},
            {"run_status": "awaitingHuman"},
            {"run_status": "awaitingLlmEvaluation"},
        ]
    )

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 3


@pytest.mark.asyncio
async def test_completed_but_retained_conversation_does_not_count() -> None:
    """Review regression: a conversation whose run concluded is history, not
    active work — a bot with only retained history must publish normally."""
    relay = _relay(
        [
            {"run_status": "completed"},
            {"run_status": "failed"},
            {"run_status": "idle"},
        ]
    )

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 0


@pytest.mark.asyncio
async def test_mixed_history_and_active_rows_counts_only_active() -> None:
    relay = _relay(
        [
            {"run_status": "completed"},
            {"run_status": "idle"},
            {"run_status": "running"},
            {"run_status": "completed"},
        ]
    )

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 1


@pytest.mark.asyncio
async def test_rows_without_run_status_do_not_count() -> None:
    """Older listings / enrichment failures omit the field entirely — treat as
    no signal (fail-open), never as active work."""
    relay = _relay([{"id": "s1", "title": "history"}, {"run_status": None}])

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 0


@pytest.mark.asyncio
async def test_unknown_future_kind_fails_open() -> None:
    relay = _relay([{"run_status": "pausedForMaintenance"}])

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 0


@pytest.mark.asyncio
async def test_service_bot_with_no_sessions_returns_zero() -> None:
    relay = _relay([])

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 0


@pytest.mark.asyncio
async def test_relay_error_returns_zero() -> None:
    """Route absent (OSS/test profiles 404), engine down, or any relay error
    never blocks the publish."""
    relay = MagicMock()
    relay.call = AsyncMock(side_effect=EngineDeviceNotReadyError("no device"))

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 0


@pytest.mark.asyncio
async def test_personal_bot_skips_check() -> None:
    relay = _relay([{"run_status": "running"}])

    count = await SessionPrechecker(relay=relay).active_session_count(_bot("personal"))

    assert count == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_without_id_is_not_checked() -> None:
    relay = _relay([{"run_status": "running"}])

    assert await SessionPrechecker(relay=relay).active_session_count({"owner_id": "u1"}) == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_without_owner_is_not_checked() -> None:
    relay = _relay([{"run_status": "running"}])

    assert await SessionPrechecker(relay=relay).active_session_count({"bot_id": "b1"}) == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_relay_is_cut_off_by_timeout() -> None:
    async def _hang(**_kwargs: object) -> EngineResult:
        await asyncio.sleep(5)
        return EngineResult(data=[])

    relay = MagicMock()
    relay.call = _hang

    checker = SessionPrechecker(relay=relay, timeout_s=0.05)
    assert await checker.active_session_count(_bot()) == 0


@pytest.mark.asyncio
async def test_non_list_data_returns_zero() -> None:
    relay = _relay({"sessions": [{"run_status": "running"}]})

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 0
