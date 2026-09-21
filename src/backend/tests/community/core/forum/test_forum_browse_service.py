"""Service-layer validation for the BBS Browse Loop (subscription + feed)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.forum.models import (
    BROWSE_MODE_OPENCLAW,
    BrowseFeedPage,
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
    BrowseSubscriptionUpsertResult,
)
from agentclaw.community.core.forum.services.forum_service import ForumService


def _record(*, mode="framework", note=None):
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseSubscriptionRecord(
        bot_id="bot-a",
        owner_user_id="111111",
        mode=mode,
        note=note,
        created_at=now,
        updated_at=now,
    )


class _Repo:
    """Records BBS Browse-Loop calls; pulses data record objects."""

    def __init__(self):
        self.calls = []

    def upsert_subscription(self, **kwargs):
        self.calls.append(("upsert_subscription", kwargs))
        return BrowseSubscriptionUpsertResult(subscription=_record(mode=kwargs["mode"]), created=True)

    def get_subscription(self, bot_id):
        self.calls.append(("get_subscription", bot_id))
        return _record()

    def list_subscriptions(self, **kwargs):
        self.calls.append(("list_subscriptions", kwargs))
        return BrowseSubscriptionPage(total=1, items=(_record(),))

    def delete_subscription(self, bot_id):
        self.calls.append(("delete_subscription", bot_id))
        return True

    def list_browse_feed(self, **kwargs):
        self.calls.append(("list_browse_feed", kwargs))
        return BrowseFeedPage(total=0, items=())


def _service():
    return ForumService(_Repo())


def test_upsert_subscription_validates_mode_and_lowercases():
    svc = _service()
    result = svc.upsert_subscription(
        bot_id="bot-a",
        owner_user_id="111111",
        mode="FRAMEWORK",
        note=None,
    )
    assert result.created is True
    last = _repo_calls(svc)[-1]
    assert last[0] == "upsert_subscription"
    assert last[1]["mode"] == "framework"


def _repo_calls(svc):
    return [c for c in svc._repository.calls]


def test_upsert_subscription_rejects_unknown_mode():
    svc = _service()
    with pytest.raises(ValidationError):
        svc.upsert_subscription(
            bot_id="bot-a",
            owner_user_id="111111",
            mode="heartbeat",
        )


def test_list_subscriptions_normalises_mode_and_pagination():
    svc = _service()
    svc.list_subscriptions(page=1, page_size=20, mode="OPENCLAW")
    last = _repo_calls(svc)[-1]
    assert last[0] == "list_subscriptions"
    assert last[1] == {"offset": 0, "limit": 20, "mode": BROWSE_MODE_OPENCLAW}

    # ``None`` mode is preserved (no filter applied).
    svc = _service()
    svc.list_subscriptions(page=3, page_size=5)
    assert _repo_calls(svc)[-1][1] == {"offset": 10, "limit": 5, "mode": None}


def test_get_and_delete_subscription_delegate_normalised_bot_id():
    svc = _service()
    svc.get_subscription(bot_id=" bot-a ")
    svc.delete_subscription(bot_id=" bot-a ")
    assert _repo_calls(svc)[-2] == ("get_subscription", "bot-a")
    assert _repo_calls(svc)[-1] == ("delete_subscription", "bot-a")


def test_list_browse_feed_validates_pagination_and_accepts_empty_filters():
    svc = _service()
    svc.list_browse_feed(
        bot_id="bot-a", status=None, topic_type=None, page=2, page_size=5
    )
    last = _repo_calls(svc)[-1]
    assert last[0] == "list_browse_feed"
    assert last[1] == {
        "bot_id": "bot-a",
        "status": None,
        "topic_type": None,
        "offset": 5,
        "limit": 5,
    }


def test_list_browse_feed_rejects_bad_status_and_type():
    svc = _service()
    with pytest.raises(ValidationError):
        svc.list_browse_feed(
            bot_id="bot-a", status="oxygen", topic_type=None, page=1, page_size=10
        )
    svc = _service()
    with pytest.raises(ValidationError):
        svc.list_browse_feed(
            bot_id="bot-a", status=None, topic_type="discussion-poll", page=1, page_size=10
        )
