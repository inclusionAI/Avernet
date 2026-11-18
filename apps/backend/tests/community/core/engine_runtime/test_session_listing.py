"""Bounded source scan with an engine that has no source filtering capability."""

import asyncio
import base64
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from agentclaw.community.core.engine_runtime.errors import EngineSessionCursorError, EngineSessionScanDepthError, EngineUpstreamError
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.core.engine_runtime.session_listing import (
    SessionSourceListingService, _decode, _encode, _matches,
)
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTimeoutError

FACTS = BotFacts(bot_id="b1", owner_id="owner", bot_type="service", active_engine="teclaw")


def row(index, user):
    return {"id": f"agent:main:session:{index}:user:{user}", "title": str(index)}


class TableRelay:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        params = kwargs["params"]
        assert "source" not in params and "user_id" not in params
        return EngineResult(data=self.rows[params["offset"]:params["offset"] + params["limit"]], total=len(self.rows))


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "aicoding", "claude_code"])
@pytest.mark.parametrize("caller", ["owner", "collaborator"])
async def test_native_mine_uses_actor_filter_and_filtered_offset(engine, caller):
    class NativeRelay(TableRelay):
        async def call(self, **kwargs):
            self.calls.append(kwargs)
            params = kwargs["params"]
            assert params["user_id"] == caller
            assert params["source"] == "all_but_others"
            return EngineResult(data=self.rows[params["offset"]:params["offset"] + params["limit"]])

    relay = NativeRelay([row(i, caller) for i in range(5)])
    service = SessionSourceListingService(relay)
    options = {"facts": replace(FACTS, active_engine=engine), "source": "mine", "caller_id": caller}
    first = await read(service, **options)
    second = await read(service, cursor=first.next_cursor, **options)
    assert len(relay.calls) == 2  # One native read per public request.
    assert [call["params"]["offset"] for call in relay.calls] == [0, 2]
    assert [item["title"] for item in first.items + second.items] == ["0", "1", "2", "3"]
    assert not first.scan_limited and not second.scan_limited


@pytest.mark.asyncio
async def test_unknown_engine_mine_falls_back_without_delegating_source():
    relay = TableRelay([row(0, "other"), row(1, "reader")])
    result = await read(SessionSourceListingService(relay), source="mine")
    assert result.items == [row(1, "reader")]


async def read(service, **overrides):
    return await service.list_sessions(**{
        "facts": FACTS, "caller_id": "reader", "stage": "online", "source": "others", "page_size": 2,
        **overrides,
    })


@pytest.mark.asyncio
async def test_cursor_consumes_candidates_not_filtered_page_offsets():
    relay = TableRelay([row(i, "reader" if i % 2 == 0 else "other") for i in range(13)])
    service = SessionSourceListingService(relay)
    cursor = None
    found = []
    for _ in range(20):
        before = len(relay.calls)
        result = await read(service, cursor=cursor)
        assert len(relay.calls) - before <= 2
        found.extend(item["id"] for item in result.items)
        assert result.total == len(found)  # Never use the unfiltered engine total.
        if not result.has_more:
            break
        cursor = result.next_cursor
        assert cursor
    else:
        pytest.fail("scan did not terminate")
    assert found == [row(i, "other")["id"] for i in range(1, 13, 2)]
    assert result.next_cursor is None


@pytest.mark.asyncio
async def test_empty_sparse_window_keeps_continuation_and_read_budget():
    relay = TableRelay([row(i, "reader") for i in range(8)] + [row(9, "other")])
    service = SessionSourceListingService(relay)
    first = await read(service)
    assert first.items == [] and first.total == 0
    assert first.scan_limited and first.has_more and first.next_cursor
    assert len(relay.calls) == 2
    assert all(0 < call["timeout"] <= 3 for call in relay.calls)
    second = await read(service, cursor=first.next_cursor)
    assert [item["id"] for item in second.items] == [row(9, "other")["id"]]


@pytest.mark.asyncio
async def test_lookahead_is_not_consumed_or_duplicated():
    relay = TableRelay([row(i, "other") for i in range(5)])
    service = SessionSourceListingService(relay)
    first = await read(service)
    assert len(relay.calls) == 1 and not first.scan_limited
    second = await read(service, cursor=first.next_cursor)
    assert [item["title"] for item in first.items + second.items] == ["0", "1", "2", "3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"caller_id": "other"}, {"facts": replace(FACTS, owner_id="other")},
    {"facts": replace(FACTS, bot_id="other")}, {"stage": "draft"},
    {"device_uuid": "other"}, {"source": "mine"}, {"page_size": 3},
    {"agent_id": "other"}, {"session_key": "other"},
])
async def test_cursor_scope_mismatch_refuses_before_device_read(override):
    relay = TableRelay([row(i, "other") for i in range(5)])
    service = SessionSourceListingService(relay)
    first = await read(service)
    calls = len(relay.calls)
    with pytest.raises(EngineSessionCursorError):
        await read(service, cursor=first.next_cursor, **override)
    assert len(relay.calls) == calls


@pytest.mark.asyncio
async def test_depth_limit_is_explicit_not_false_exhaustion():
    relay = TableRelay([row(i, "reader") for i in range(2100)])
    service = SessionSourceListingService(relay)
    first = await read(service)
    _, _, _, scope = json.loads(base64.urlsafe_b64decode(first.next_cursor + "=" * (-len(first.next_cursor) % 4)))
    last = await read(service, cursor=_encode(1999, 0, scope))
    assert last.items == [] and last.scan_limited and last.has_more
    with pytest.raises(EngineSessionScanDepthError):
        await read(service, cursor=last.next_cursor)


@pytest.mark.parametrize("cursor", ["!", "null", "W10", "a" * 513])
def test_bad_cursor(cursor):
    with pytest.raises(EngineSessionCursorError):
        _decode(cursor, "scope")


@pytest.mark.parametrize("key,is_mine", [
    ("agent:main:session:1:user:reader", True),
    ("user:reader:session:1:agent:b1", True),
    ("agent:main:dingtalk:direct:other", False),
    ("agent:default:default:c1:user:other", False),
    ("system:unknown", True), ("bcs-cli:default:owner:123", True),
    ("user:collab:session:1:agent:member-task", True),
])
def test_creator_rule_does_not_use_engine_reader_field(key, is_mine):
    item = {"id": key, "user_id": "reader"}
    assert _matches(item, "reader", "mine") is is_mine
    assert _matches(item, "reader", "others") is not is_mine


def test_numeric_user_normalization():
    assert _matches(row(1, "00123"), "123", "mine")
    assert not _matches(row(1, "00123"), "123", "others")


@pytest.mark.asyncio
async def test_timeout_is_engine_timeout_not_internal_error():
    class SlowRelay:
        async def call(self, **kwargs):
            await asyncio.sleep(0.1)
    with patch("agentclaw.community.core.engine_runtime.session_listing.READ_BUDGET_SECONDS", 0.01):
        with pytest.raises(DeviceAdapterTimeoutError):
            await read(SessionSourceListingService(SlowRelay()))


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {}, ["bad"], [{"title": "missing id"}]])
async def test_malformed_candidates_fail_instead_of_false_last_page(payload):
    class BadRelay:
        async def call(self, **kwargs):
            return EngineResult(data=payload)
    with pytest.raises(EngineUpstreamError):
        await read(SessionSourceListingService(BadRelay()))
