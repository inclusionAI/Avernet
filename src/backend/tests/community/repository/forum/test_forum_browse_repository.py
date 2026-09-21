"""BBS Browse Loop repository: subscription CRUD + actor-aware feed.

Uses the existing in-memory SQLite fixture to exercise the real SQL paths —
notably the correlated-subquery exclusion of POLL/NOTICE topics after the actor
replied.
"""

from __future__ import annotations

from agentclaw.community.core.forum.models import (
    AUTHOR_TYPE_BOT,
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
    TOPIC_TYPE_DISCUSSION,
    TOPIC_TYPE_NOTICE,
    TOPIC_TYPE_POLL,
)
from agentclaw.community.core.repository.implementations.forum.forum_repository import (
    ForumRepository,
)


def _create_topic(repo, *, topic_id, title=None, body=None, topic_type="DISCUSSION"):
    return repo.create_topic(
        author_type=AUTHOR_TYPE_BOT,
        author_id="bot-publisher",
        client_request_id=f"req-{topic_id}",
        title=title or f"Topic {topic_id}",
        body=body or "body",
        topic_type=topic_type,
    )


def _reply(repo, *, topic_id, author_id="bot-a", request_id="rep-1"):
    return repo.create_reply(
        topic_id=topic_id,
        author_type=AUTHOR_TYPE_BOT,
        author_id=author_id,
        client_request_id=request_id,
        body="reply",
    )


def test_upsert_subscription_creates_then_replaces(db):
    repo = ForumRepository(db)

    first = repo.upsert_subscription(
        bot_id="bot-a",
        owner_user_id="111111",
        mode=BROWSE_MODE_FRAMEWORK,
        note=None,
    )
    assert first.created is True
    assert first.subscription.bot_id == "bot-a"
    assert first.subscription.mode == "framework"
    assert first.subscription.note is None

    second = repo.upsert_subscription(
        bot_id="bot-a",
        owner_user_id="111111",
        mode=BROWSE_MODE_OPENCLAW,
        note="self cron",
    )
    assert second.created is False
    assert second.subscription.bot_id == "bot-a"
    assert second.subscription.mode == "openclaw"
    assert second.subscription.note == "self cron"


def test_get_subscription_missing_returns_none(db):
    repo = ForumRepository(db)
    assert repo.get_subscription("bot-a") is None


def test_list_subscriptions_filters_by_mode(db):
    repo = ForumRepository(db)
    repo.upsert_subscription(
        bot_id="bot-a", owner_user_id="1", mode=BROWSE_MODE_FRAMEWORK
    )
    repo.upsert_subscription(
        bot_id="bot-b", owner_user_id="2", mode=BROWSE_MODE_OPENCLAW
    )

    all_subs = repo.list_subscriptions(offset=0, limit=10)
    assert all_subs.total == 2

    framework = repo.list_subscriptions(offset=0, limit=10, mode=BROWSE_MODE_FRAMEWORK)
    assert framework.total == 1
    assert framework.items[0].bot_id == "bot-a"


def test_delete_subscription_idempotent(db):
    repo = ForumRepository(db)
    repo.upsert_subscription(
        bot_id="bot-a", owner_user_id="1", mode=BROWSE_MODE_FRAMEWORK
    )

    assert repo.delete_subscription("bot-a") is True
    # Second delete — there is nothing more to remove.
    assert repo.delete_subscription("bot-a") is False


def test_feed_returns_open_discussion_and_untouched_poll_or_notice(db):
    repo = ForumRepository(db)
    disc = _create_topic(repo, topic_id="d1", topic_type=TOPIC_TYPE_DISCUSSION)
    poll = _create_topic(repo, topic_id="p1", topic_type=TOPIC_TYPE_POLL)
    notice = _create_topic(repo, topic_id="n1", topic_type=TOPIC_TYPE_NOTICE)

    feed = repo.list_browse_feed(
        bot_id="bot-a", status=None, topic_type=None, offset=0, limit=10
    )
    ids = {item.topic_id for item in feed.items}
    assert ids == {disc.topic.topic_id, poll.topic.topic_id, notice.topic.topic_id}
    # All three still count 0 replies from bot-a.
    by_id = {item.topic_id: item for item in feed.items}
    assert by_id[poll.topic.topic_id].my_reply_count == 0


def test_feed_excludes_poll_and_notice_after_actor_replies(db):
    repo = ForumRepository(db)
    disc = _create_topic(repo, topic_id="d1", topic_type=TOPIC_TYPE_DISCUSSION)
    poll = _create_topic(repo, topic_id="p1", topic_type=TOPIC_TYPE_POLL)
    notice = _create_topic(repo, topic_id="n1", topic_type=TOPIC_TYPE_NOTICE)

    # bot-a replies in all three.
    _reply(repo, topic_id=disc.topic.topic_id, author_id="bot-a", request_id="r-d")
    _reply(repo, topic_id=poll.topic.topic_id, author_id="bot-a", request_id="r-p")
    _reply(repo, topic_id=notice.topic.topic_id, author_id="bot-a", request_id="r-n")

    feed = repo.list_browse_feed(
        bot_id="bot-a", status=None, topic_type=None, offset=0, limit=10
    )
    ids = {item.topic_id for item in feed.items}
    # DISCUSSION is repeatable → still surfaced; POLL/NOTICE are not.
    assert disc.topic.topic_id in ids
    assert poll.topic.topic_id not in ids
    assert notice.topic.topic_id not in ids
    by_id = {item.topic_id: item for item in feed.items}
    assert by_id[disc.topic.topic_id].my_reply_count == 1


def test_feed_filters_by_status_and_topic_type(db):
    repo = ForumRepository(db)
    disc_open = _create_topic(repo, topic_id="d1", topic_type=TOPIC_TYPE_DISCUSSION)
    poll = _create_topic(repo, topic_id="p1", topic_type=TOPIC_TYPE_POLL)

    only_poll = repo.list_browse_feed(
        bot_id="bot-a", status=None, topic_type=TOPIC_TYPE_POLL, offset=0, limit=10
    )
    assert only_poll.total == 1
    assert only_poll.items[0].topic_id == poll.topic.topic_id and only_poll.items[0].topic_type == TOPIC_TYPE_POLL

    only_poll_filter_unused = repo.list_browse_feed(
        bot_id="bot-a", status="OPEN", topic_type=None, offset=0, limit=10
    )
    assert disc_open.topic.topic_id in {i.topic_id for i in only_poll_filter_unused.items}
