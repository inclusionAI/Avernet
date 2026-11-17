"""SessionPrechecker: consumption point parked until the engine exposes a
running-work signal (Dima 2026090100118725722). Until then the count is
always 0 — retained history can never block a publish, and no route that
does not exist is invented."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.service_bot.services.publish_flow.session_precheck import (
    SessionPrechecker,
)


def _bot(bot_type: str = "service") -> dict:
    return {"bot_id": "b1", "owner_id": "u1", "bot_type": bot_type}


def _relay() -> MagicMock:
    relay = MagicMock()
    relay.call = AsyncMock()
    return relay


@pytest.mark.asyncio
async def test_reports_zero_without_calling_any_engine_route() -> None:
    """No running-work signal exists on the engine today: the precheck must
    not fabricate one (plain session listings count history, not work), so it
    reports 0 and never dials the relay."""
    relay = _relay()

    assert await SessionPrechecker(relay=relay).active_session_count(_bot()) == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_bots_also_report_zero() -> None:
    relay = _relay()

    assert await SessionPrechecker(relay=relay).active_session_count(_bot("personal")) == 0
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_bot_records_report_zero() -> None:
    relay = _relay()

    assert await SessionPrechecker(relay=relay).active_session_count({}) == 0
    relay.call.assert_not_awaited()


def test_relay_consumption_point_stays_wired() -> None:
    """The DI entry survives: once the engine-side Active Session API lands,
    the real query issues through this relay — one method changes, not the
    wiring."""
    relay = _relay()

    checker = SessionPrechecker(relay=relay)

    assert checker.relay is relay
