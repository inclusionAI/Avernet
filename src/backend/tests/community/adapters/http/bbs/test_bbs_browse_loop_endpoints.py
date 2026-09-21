"""Endpoints for the BBS Browse Loop — subscription CRUD + actor-aware feed.

Mirrors the existing internal-router delegate-test style: each handler is
invoked directly with an in-process stub :class:`ForumServiceProtocol`, so we
exercise the wiring, idempotency status codes and envelope shape without booting
the full injector.
"""

from __future__ import annotations

from typing import Any

from datetime import datetime, timezone

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.bbs.router import (
    delete_subscription_internal,
    get_subscription_by_bot_internal,
    get_subscription_internal,
    list_browse_feed_internal,
    list_subscriptions_internal,
    trigger_cron_register_internal,
    trigger_cron_remove_internal,
    trigger_framework_browse_internal,
    trigger_self_browse_internal,
    upsert_subscription_internal,
)
from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    BrowseFeedTopicItem,
    SubscriptionItem,
    UpsertSubscriptionRequest,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.forum.models import (
    BrowseFeedPage,
    BrowseFeedTopicRecord,
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
    BrowseSubscriptionUpsertResult,
)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-browse-loop"
    return request


def _record(*, bot_id: str = "bot-a", mode: str = "framework", note=None):
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseSubscriptionRecord(
        bot_id=bot_id,
        owner_user_id="111111",
        mode=mode,
        note=note,
        created_at=now,
        updated_at=now,
    )


def _feed_item(*, topic_id: str, my_reply_count: int, topic_type: str = "DISCUSSION"):
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseFeedTopicRecord(
        topic_id=topic_id,
        author_type="BOT",
        author_id="bot-b",
        title="Pending topic",
        body_preview="hello".ljust(500, " ")[:500],
        body_truncated=False,
        status="OPEN",
        topic_type=topic_type,
        created_at=now,
        updated_at=now,
        my_reply_count=my_reply_count,
    )


@pytest.mark.asyncio
async def test_upsert_subscription_returns_201_when_created():
    class Service:
        def upsert_subscription(self, **kwargs):
            assert kwargs == {
                "bot_id": "bot-a",
                "owner_user_id": "111111",
                "mode": "framework",
                "note": None,
            }
            return BrowseSubscriptionUpsertResult(subscription=_record(), created=True)

    response = Response()
    payload = await upsert_subscription_internal(
        body=UpsertSubscriptionRequest(owner_user_id="111111", mode="framework"),
        bot_id="bot-a",
        request=_request("POST", "/api/v1/bots/bot-a/bbs/browse-subscription"),
        response=response,
        service=Service(),
    )
    assert response.status_code == 201
    assert payload.data is not None and payload.data.bot_id == "bot-a"


@pytest.mark.asyncio
async def test_upsert_subscription_returns_200_when_replaced():
    class Service:
        def upsert_subscription(self, **kwargs):
            assert kwargs["mode"] == "openclaw"
            return BrowseSubscriptionUpsertResult(
                subscription=_record(mode="openclaw", note="self cron"),
                created=False,
            )

    response = Response()
    payload = await upsert_subscription_internal(
        body=UpsertSubscriptionRequest(
            owner_user_id="111111", mode="openclaw", note="self cron"
        ),
        bot_id="bot-a",
        request=_request("POST", "/api/v1/bots/bot-a/bbs/browse-subscription"),
        response=response,
        service=Service(),
    )
    assert response.status_code == 200
    assert isinstance(payload.data, SubscriptionItem)
    assert payload.data.mode == "openclaw" and payload.data.note == "self cron"


@pytest.mark.asyncio
async def test_get_subscription_returns_envelope_when_found():
    class Service:
        def get_subscription(self, **kwargs):
            assert kwargs == {"bot_id": "bot-a"}
            return _record()

    payload = await get_subscription_internal(
        bot_id="bot-a",
        request=_request("GET", "/api/v1/bots/bot-a/bbs/browse-subscription"),
        service=Service(),
    )
    assert payload.data is not None and payload.data.owner_user_id == "111111"


@pytest.mark.asyncio
async def test_get_subscription_raises_not_found_when_missing():
    class Service:
        def get_subscription(self, **kwargs):
            return None

    with pytest.raises(NotFound):
        await get_subscription_internal(
            bot_id="bot-a",
            request=_request("GET", "/api/v1/bots/bot-a/bbs/browse-subscription"),
            service=Service(),
        )


@pytest.mark.asyncio
async def test_delete_subscription_returns_deleted_flag():
    class Service:
        def delete_subscription(self, **kwargs):
            assert kwargs == {"bot_id": "bot-a"}
            return True

    payload = await delete_subscription_internal(
        bot_id="bot-a",
        request=_request("DELETE", "/api/v1/bots/bot-a/bbs/browse-subscription"),
        service=Service(),
    )
    assert payload.data is not None and payload.data.deleted is True


@pytest.mark.asyncio
async def test_list_browse_feed_delegates_pagination_and_filters():
    class Service:
        def list_browse_feed(self, **kwargs):
            assert kwargs == {
                "bot_id": "bot-a",
                "status": "OPEN",
                "topic_type": None,
                "page": 1,
                "page_size": 20,
            }
            return BrowseFeedPage(total=2, items=(_feed_item(topic_id="topic_1", my_reply_count=0), _feed_item(topic_id="topic_2", my_reply_count=3, topic_type="DISCUSSION")))

    payload = await list_browse_feed_internal(
        bot_id="bot-a",
        request=_request("GET", "/api/v1/bots/bot-a/bbs/feed"),
        page_params=PageParams(),
        status="OPEN",
        topic_type=None,
        service=Service(),
    )
    assert payload.data is not None and payload.data.total == 2
    assert isinstance(payload.data.items[0], BrowseFeedTopicItem)
    assert payload.data.items[0].my_reply_count == 0


@pytest.mark.asyncio
async def test_list_subscriptions_routes_to_tenant_listing():
    class Service:
        def list_subscriptions(self, **kwargs):
            assert kwargs == {"page": 1, "page_size": 20, "mode": "framework"}
            return BrowseSubscriptionPage(total=1, items=(_record(),))

    payload = await list_subscriptions_internal(
        request=_request("GET", "/api/v1/bbs/browse-loop/subscriptions"),
        page_params=PageParams(),
        mode="framework",
        service=Service(),
    )
    assert payload.data is not None and payload.data.total == 1


@pytest.mark.asyncio
async def test_get_subscription_by_bot_reviews_under_read_router():
    class Service:
        def get_subscription(self, **kwargs):
            assert kwargs == {"bot_id": "bot-a"}
            return _record()

    payload = await get_subscription_by_bot_internal(
        bot_id="bot-a",
        request=_request("GET", "/api/v1/bbs/browse-loop/subscriptions/bot-a"),
        service=Service(),
    )
    assert payload.data is not None and payload.data.bot_id == "bot-a"


# ---------------------------------------------------------------------------
# Manual triggers — A (framework) / B (openclaw self-cron) navigate the same
# runner seam. Each handler is invoked directly with a stub runner so we assert
# the right mode/action is forwarded without booting the injector.
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Records push_* calls; returns canned BotSendResult-like dicts."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    async def push_browse_once(self, *, bot_id, expected_mode, timeout=60.0):
        self.calls.append({"op": "browse", "bot_id": bot_id, "expected_mode": expected_mode})
        if self._fail == "validation":
            from agentclaw.community.core.errors import ValidationError
            raise ValidationError("mode mismatch (test)")
        return {"run_id": "run_b_x", "session_id": "sess_b_x"}

    async def push_cron_event(self, *, bot_id, action, timeout=30.0):
        self.calls.append({"op": "cron", "bot_id": bot_id, "action": action})
        return {"run_id": "run_cron", "session_id": None}


@pytest.mark.asyncio
async def test_trigger_framework_pushes_with_framework_mode():
    runner = _FakeRunner()
    payload = await trigger_framework_browse_internal(
        request=_request("POST", "/api/v1/bbs/browse-loop/trigger-framework"),
        bot_id="bot-a",
        runner=runner,
    )
    assert runner.calls == [{"op": "browse", "bot_id": "bot-a", "expected_mode": "framework"}]
    assert payload.data == {"run_id": "run_b_x", "session_id": "sess_b_x"}


@pytest.mark.asyncio
async def test_trigger_self_pushes_with_openclaw_mode():
    runner = _FakeRunner()
    payload = await trigger_self_browse_internal(
        bot_id="bot-a",
        request=_request("POST", "/api/v1/bots/bot-a/bbs/browse-loop/trigger-self"),
        runner=runner,
    )
    assert runner.calls == [{"op": "browse", "bot_id": "bot-a", "expected_mode": "openclaw"}]
    assert payload.data is not None and payload.data["run_id"] == "run_b_x"


@pytest.mark.asyncio
async def test_trigger_cron_register_forwards_register_action():
    runner = _FakeRunner()
    payload = await trigger_cron_register_internal(
        bot_id="bot-a",
        request=_request("POST", "/api/v1/bots/bot-a/bbs/browse-loop/cron-register"),
        runner=runner,
    )
    assert runner.calls == [{"op": "cron", "bot_id": "bot-a", "action": "register"}]
    assert payload.data is not None and payload.data["run_id"] == "run_cron"


@pytest.mark.asyncio
async def test_trigger_cron_remove_forwards_remove_action():
    runner = _FakeRunner()
    await trigger_cron_remove_internal(
        bot_id="bot-a",
        request=_request("POST", "/api/v1/bots/bot-a/bbs/browse-loop/cron-remove"),
        runner=runner,
    )
    assert runner.calls == [{"op": "cron", "bot_id": "bot-a", "action": "remove"}]


@pytest.mark.asyncio
async def test_trigger_framework_propagates_runner_validation_error():
    """Mode-mismatch surfaced by the runner re-raises out of @envelope_errors.

    The generic core NotFound/ValidationError are NOT in ENVELOPE_ERRORS, so
    @envelope_errors re-raises them. Invoking the handler directly then asserts
    the runner's ValidationError bubbles up unchanged.
    """
    from agentclaw.community.core.errors import ValidationError
    runner = _FakeRunner(fail="validation")
    with pytest.raises(ValidationError):
        await trigger_framework_browse_internal(
            request=_request("POST", "/api/v1/bbs/browse-loop/trigger-framework"),
            bot_id="bot-a",
            runner=runner,
        )
