"""Public ``/openapi/v1/bots/{bot_id}/bbs/*`` reads + manual-trigger handlers.

Mirrors ``test_bbs_browse_loop_endpoints.py`` (the internal ``/api/v1`` twin)
but exercises the public openapi handlers, which take only a verified
principal at the route level (mounted in ``_OPEN_SUBGROUPS``) and the addressed
``bot_id`` path key — no owner on the wire, admission OPEN / authorization
NoCheck. Asserted directly: each handler delegates to the same
``ForumServiceProtocol`` / ``BbsBrowseLoopRunner`` seam as the internal twins.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.bbs.browse_router import (
    get_bbs_browse_subscription,
    list_bbs_browse_feed,
    trigger_bbs_browse_cron_register,
    trigger_bbs_browse_cron_remove,
    trigger_bbs_browse_framework,
    trigger_bbs_browse_self,
)
from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    BrowseFeedTopicItem,
    SubscriptionItem,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.forum.models import (
    BrowseFeedPage,
    BrowseFeedTopicRecord,
    BrowseSubscriptionRecord,
)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-browse-open"
    return request


def _record(*, bot_id: str = "bot-a", mode: str = "openclaw") -> BrowseSubscriptionRecord:
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseSubscriptionRecord(
        bot_id=bot_id,
        owner_user_id="111111",
        mode=mode,
        note=None,
        created_at=now,
        updated_at=now,
    )


def _feed_item(*, topic_id: str, my_reply_count: int) -> BrowseFeedTopicRecord:
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseFeedTopicRecord(
        topic_id=topic_id,
        author_type="BOT",
        author_id="bot-b",
        title="Pending topic",
        body_preview="hello".ljust(500, " ")[:500],
        body_truncated=False,
        status="OPEN",
        topic_type="DISCUSSION",
        created_at=now,
        updated_at=now,
        my_reply_count=my_reply_count,
    )


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def push_browse_once(self, *, bot_id, expected_mode, timeout=60.0):
        self.calls.append({"op": "browse", "bot_id": bot_id, "expected_mode": expected_mode})
        return {"run_id": "run_x", "session_id": "sess_x"}

    async def push_cron_event(self, *, bot_id, action, timeout=30.0):
        self.calls.append({"op": "cron", "bot_id": bot_id, "action": action})
        return {"run_id": "run_cron", "session_id": None}


@pytest.mark.asyncio
async def test_get_bbs_browse_subscription_returns_envelope_when_found():
    class Service:
        def get_subscription(self, **kwargs):
            assert kwargs == {"bot_id": "bot-a"}
            return _record()

    payload = await get_bbs_browse_subscription(
        bot_id="bot-a",
        request=_request("GET", "/openapi/v1/bots/bot-a/bbs/browse-subscription"),
        service=Service(),
    )
    assert payload.data is not None
    assert isinstance(payload.data, SubscriptionItem)
    assert payload.data.bot_id == "bot-a"
    assert payload.data.owner_user_id == "111111"


@pytest.mark.asyncio
async def test_get_bbs_browse_subscription_raises_not_found_when_missing():
    class Service:
        def get_subscription(self, **kwargs):
            return None

    with pytest.raises(NotFound):
        await get_bbs_browse_subscription(
            bot_id="bot-a",
            request=_request("GET", "/openapi/v1/bots/bot-a/bbs/browse-subscription"),
            service=Service(),
        )


@pytest.mark.asyncio
async def test_list_bbs_browse_feed_delegates_pagination_and_filters():
    class Service:
        def list_browse_feed(self, **kwargs):
            assert kwargs == {
                "bot_id": "bot-a",
                "status": "OPEN",
                "topic_type": None,
                "page": 1,
                "page_size": 20,
            }
            return BrowseFeedPage(
                total=2,
                items=(_feed_item(topic_id="t1", my_reply_count=0), _feed_item(topic_id="t2", my_reply_count=3)),
            )

    payload = await list_bbs_browse_feed(
        bot_id="bot-a",
        request=_request("GET", "/openapi/v1/bots/bot-a/bbs/feed"),
        page_params=PageParams(),
        status="OPEN",
        topic_type=None,
        service=Service(),
    )
    assert payload.data is not None
    assert payload.data.total == 2
    assert isinstance(payload.data.items[0], BrowseFeedTopicItem)
    assert payload.data.items[0].my_reply_count == 0


@pytest.mark.asyncio
async def test_trigger_bbs_browse_framework_pushes_with_framework_mode():
    runner = _FakeRunner()
    payload = await trigger_bbs_browse_framework(
        bot_id="bot-a",
        request=_request("POST", "/openapi/v1/bots/bot-a/bbs/browse-loop/trigger-framework"),
        runner=runner,
    )
    assert runner.calls == [{"op": "browse", "bot_id": "bot-a", "expected_mode": "framework"}]
    assert payload.data == {"run_id": "run_x", "session_id": "sess_x"}


@pytest.mark.asyncio
async def test_trigger_bbs_browse_self_pushes_with_openclaw_mode():
    runner = _FakeRunner()
    await trigger_bbs_browse_self(
        bot_id="bot-a",
        request=_request("POST", "/openapi/v1/bots/bot-a/bbs/browse-loop/trigger-self"),
        runner=runner,
    )
    assert runner.calls == [{"op": "browse", "bot_id": "bot-a", "expected_mode": "openclaw"}]


@pytest.mark.asyncio
async def test_trigger_bbs_browse_cron_register_forwards_register_action():
    runner = _FakeRunner()
    await trigger_bbs_browse_cron_register(
        bot_id="bot-a",
        request=_request("POST", "/openapi/v1/bots/bot-a/bbs/browse-loop/cron-register"),
        runner=runner,
    )
    assert runner.calls == [{"op": "cron", "bot_id": "bot-a", "action": "register"}]


@pytest.mark.asyncio
async def test_trigger_bbs_browse_cron_remove_forwards_remove_action():
    runner = _FakeRunner()
    await trigger_bbs_browse_cron_remove(
        bot_id="bot-a",
        request=_request("POST", "/openapi/v1/bots/bot-a/bbs/browse-loop/cron-remove"),
        runner=runner,
    )
    assert runner.calls == [{"op": "cron", "bot_id": "bot-a", "action": "remove"}]
