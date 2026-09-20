from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.forum.models import (
    TOPIC_STATUS_CLOSED,
    TOPIC_TYPE_NOTICE,
    TOPIC_TYPE_POLL,
)
from agentclaw.community.core.forum.repository.models import (
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.core.repository.implementations.forum.forum_repository import (
    ForumRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def _topic(repo, request_id, *, title, body="description", topic_type="DISCUSSION"):
    return repo.create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id=request_id,
        title=title,
        body=body,
        topic_type=topic_type,
    ).topic


def _reply(repo, topic_id, request_id, body):
    return repo.create_reply(
        topic_id=topic_id,
        author_type="BOT",
        author_id="bot-b",
        client_request_id=request_id,
        body=body,
    ).post


def test_list_topics_filters_status_searches_literal_wildcards_and_pages(db):
    repo = ForumRepository(db)
    first = _topic(repo, "req-1", title="100% ready", body="plain")
    second = _topic(repo, "req-2", title="other", body="uses_name")
    _topic(repo, "req-3", title="ordinary", body="plain")
    with db.orm_session() as session:
        session.query(ForumTopicModel).filter_by(
            topic_id=second.topic_id
        ).one().status = TOPIC_STATUS_CLOSED

    percent = repo.list_topics(keyword="%", status=None, offset=0, limit=20)
    underscore = repo.list_topics(keyword="_", status=None, offset=0, limit=20)
    closed = repo.list_topics(
        keyword=None, status=TOPIC_STATUS_CLOSED, offset=0, limit=20
    )
    beyond = repo.list_topics(keyword=None, status=None, offset=10, limit=2)

    assert percent.total == 1
    assert [item.topic_id for item in percent.items] == [first.topic_id]
    assert underscore.total == 1
    assert [item.topic_id for item in underscore.items] == [second.topic_id]
    assert closed.total == 1
    assert [item.topic_id for item in closed.items] == [second.topic_id]
    assert beyond.total == 3
    assert beyond.items == ()


def test_list_topics_topic_type_filter_is_optional_and_combines_with_status(db):
    repo = ForumRepository(db)
    discussion = _topic(repo, "discussion", title="discussion")
    poll = _topic(repo, "poll", title="poll", topic_type=TOPIC_TYPE_POLL)
    notice = _topic(repo, "notice", title="notice", topic_type=TOPIC_TYPE_NOTICE)
    with db.orm_session() as session:
        session.query(ForumTopicModel).filter_by(
            topic_id=notice.topic_id
        ).one().status = TOPIC_STATUS_CLOSED

    all_types = repo.list_topics(
        keyword=None, status=None, topic_type=None, offset=0, limit=20
    )
    polls = repo.list_topics(
        keyword=None, status=None, topic_type=TOPIC_TYPE_POLL, offset=0, limit=20
    )
    closed_notices = repo.list_topics(
        keyword=None,
        status=TOPIC_STATUS_CLOSED,
        topic_type=TOPIC_TYPE_NOTICE,
        offset=0,
        limit=20,
    )

    assert {item.topic_id for item in all_types.items} == {
        discussion.topic_id,
        poll.topic_id,
        notice.topic_id,
    }
    assert [item.topic_id for item in polls.items] == [poll.topic_id]
    assert [item.topic_id for item in closed_notices.items] == [notice.topic_id]


def test_list_topics_uses_id_as_stable_tie_breaker(db):
    repo = ForumRepository(db)
    first = _topic(repo, "req-1", title="first")
    second = _topic(repo, "req-2", title="second")
    same_time = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    with db.orm_session() as session:
        session.query(ForumTopicModel).update({ForumTopicModel.gmt_create: same_time})

    result = repo.list_topics(keyword=None, status=None, offset=0, limit=20)

    assert [item.topic_id for item in result.items] == [second.topic_id, first.topic_id]


def test_topic_reads_are_isolated_by_tenant_and_environment(db, monkeypatch):
    repo = ForumRepository(db)
    with avernet_tenant_scope("tenant-a"):
        tenant_topic = _topic(repo, "tenant-a-topic", title="tenant A")
    with avernet_tenant_scope("tenant-b"):
        _topic(repo, "tenant-b-topic", title="tenant B")

    with avernet_tenant_scope("tenant-a"):
        assert repo.get_topic(tenant_topic.topic_id) is not None
        assert (
            repo.list_topics(keyword=None, status=None, offset=0, limit=20).total == 1
        )
    with avernet_tenant_scope("tenant-b"):
        assert repo.get_topic(tenant_topic.topic_id) is None
        assert (
            repo.list_topics(keyword=None, status=None, offset=0, limit=20).total == 1
        )

    monkeypatch.setenv("SERVER_ENV", "pre")
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_topic(tenant_topic.topic_id) is None
        assert (
            repo.list_topics(keyword=None, status=None, offset=0, limit=20).total == 0
        )


def test_list_posts_orders_chronologically_and_keeps_total_on_empty_page(db):
    repo = ForumRepository(db)
    topic = _topic(repo, "topic", title="topic")
    first = _reply(repo, topic.topic_id, "post-1", "first")
    second = _reply(repo, topic.topic_id, "post-2", "second")
    same_time = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    with db.orm_session() as session:
        session.query(ForumPostModel).update({ForumPostModel.gmt_create: same_time})

    result = repo.list_posts(topic_id=topic.topic_id, offset=0, limit=20)
    beyond = repo.list_posts(topic_id=topic.topic_id, offset=10, limit=20)

    assert result.total == 2
    assert [item.post_id for item in result.items] == [first.post_id, second.post_id]
    assert beyond.total == 2
    assert beyond.items == ()


def test_list_posts_allows_closed_topic_but_hides_cross_tenant_topic(db):
    repo = ForumRepository(db)
    with avernet_tenant_scope("tenant-a"):
        topic = _topic(repo, "topic", title="topic")
        reply = _reply(repo, topic.topic_id, "post", "reply")
        with db.orm_session() as session:
            session.query(ForumTopicModel).filter_by(
                topic_id=topic.topic_id
            ).one().status = TOPIC_STATUS_CLOSED
        result = repo.list_posts(topic_id=topic.topic_id, offset=0, limit=20)
        assert [item.post_id for item in result.items] == [reply.post_id]

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(NotFound, match="topic not found"):
            repo.list_posts(topic_id=topic.topic_id, offset=0, limit=20)
