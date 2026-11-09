"""EngineSessionActivityReader: newest session time via the engine-neutral route."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.bot_overview.session_activity import (
    SESSION_SCAN_LIMIT,
    SESSION_SCAN_MAX_PAGES,
    EngineSessionActivityReader,
    newest_session_time,
)
from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError
from agentclaw.community.core.engine_runtime.models import EngineResult


def _bot(bot_type: str = "personal") -> dict:
    return {"bot_id": "b1", "owner_id": "u1", "bot_type": bot_type}


def _relay(data: object) -> MagicMock:
    relay = MagicMock()
    relay.call = AsyncMock(return_value=EngineResult(data=data))
    return relay


def test_newest_session_time_takes_the_max_aware_time() -> None:
    rows = [
        {"gmt_modified": "2026-11-01T08:00:00+08:00", "title": "secret"},
        {"gmt_modified": "2026-11-02T01:00:00Z"},
        {"gmt_modified": "2026-11-03T00:00:00"},  # naive: zone unknown, skipped
        {"gmt_modified": ""},
        "not-a-row",
    ]
    assert newest_session_time(rows) == datetime(2026, 11, 2, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("data", [None, [], {"sessions": []}, [{"id": "s1"}]])
def test_newest_session_time_reports_none_without_times(data: object) -> None:
    assert newest_session_time(data) is None


@pytest.mark.asyncio
async def test_reads_the_engine_session_route_as_the_bots_owner() -> None:
    relay = _relay([{"gmt_modified": "2026-11-02T01:00:00+00:00"}])

    result = await EngineSessionActivityReader(relay=relay).last_session_at(_bot())

    assert result == datetime(2026, 11, 2, 1, tzinfo=timezone.utc)
    kwargs = relay.call.await_args.kwargs
    assert kwargs["bot_id"] == "b1"
    assert kwargs["owner_id"] == "u1"
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == "/api/sessions"
    assert kwargs["params"] == {"limit": SESSION_SCAN_LIMIT, "offset": 0}
    assert kwargs["stage"] == "draft"


@pytest.mark.asyncio
async def test_service_bot_reads_its_online_runtime() -> None:
    relay = _relay([])

    await EngineSessionActivityReader(relay=relay).last_session_at(_bot("service"))

    assert relay.call.await_args.kwargs["stage"] == "online"


@pytest.mark.asyncio
async def test_unreachable_runtime_reports_no_time() -> None:
    relay = MagicMock()
    relay.call = AsyncMock(side_effect=EngineDeviceNotReadyError("no device"))

    assert await EngineSessionActivityReader(relay=relay).last_session_at(_bot()) is None


@pytest.mark.asyncio
async def test_slow_runtime_is_cut_off() -> None:
    async def _hang(**_kwargs: object) -> EngineResult:
        await asyncio.sleep(5)
        return EngineResult(data=[])

    relay = MagicMock()
    relay.call = _hang

    reader = EngineSessionActivityReader(relay=relay, timeout_s=0.05)
    assert await reader.last_session_at(_bot()) is None


@pytest.mark.asyncio
async def test_bot_without_owner_is_not_forwarded() -> None:
    relay = _relay([])

    assert (
        await EngineSessionActivityReader(relay=relay).last_session_at({"bot_id": "b1"})
        is None
    )
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_keeps_paging_past_a_page_the_engine_filtered_empty() -> None:
    # Claude Code pages before dropping BCS group sessions, so a page can come
    # back empty while an older ordinary session exists.
    relay = MagicMock()
    relay.call = AsyncMock(
        side_effect=[
            EngineResult(data=[]),
            EngineResult(data=[{"gmt_modified": "2026-10-30T09:00:00+00:00"}]),
        ]
    )

    result = await EngineSessionActivityReader(relay=relay).last_session_at(_bot())

    assert result == datetime(2026, 10, 30, 9, tzinfo=timezone.utc)
    offsets = [c.kwargs["params"]["offset"] for c in relay.call.await_args_list]
    assert offsets == [0, SESSION_SCAN_LIMIT]


@pytest.mark.asyncio
async def test_stops_at_the_first_page_with_a_time() -> None:
    relay = _relay([{"gmt_modified": "2026-11-02T01:00:00+00:00"}])

    await EngineSessionActivityReader(relay=relay).last_session_at(_bot())

    assert relay.call.await_count == 1


@pytest.mark.asyncio
async def test_bot_without_sessions_scans_a_bounded_number_of_pages() -> None:
    relay = _relay([])

    assert await EngineSessionActivityReader(relay=relay).last_session_at(_bot()) is None
    assert relay.call.await_count == SESSION_SCAN_MAX_PAGES
