from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import NotFound, ValidationError
from agentclaw.community.core.forum.models import (
    ForumPostRecord,
    ForumTopicPage,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicRecord,
)
from agentclaw.community.core.forum.services.forum_service import ForumService


class FakeForumRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_topic(self, **kwargs):
        now = datetime(2026, 9, 20, tzinfo=timezone.utc)
        self.calls.append({"operation": "create_topic", **kwargs})
        return ForumTopicCreateResult(
            topic=ForumTopicRecord(
                topic_id="topic_created",
                author_type=kwargs["author_type"],
                author_id=kwargs["author_id"],
                title=kwargs["title"],
                body=kwargs["body"],
                status="OPEN",
                created_at=now,
                updated_at=now,
            ),
            created=True,
        )

    def create_reply(self, **kwargs):
        now = datetime(2026, 9, 20, tzinfo=timezone.utc)
        self.calls.append({"operation": "create_reply", **kwargs})
        return ForumReplyCreateResult(
            post=ForumPostRecord(
                post_id="post_created",
                topic_id=kwargs["topic_id"],
                author_type=kwargs["author_type"],
                author_id=kwargs["author_id"],
                body=kwargs["body"],
                created_at=now,
                updated_at=now,
            ),
            created=True,
        )

    def get_topic(self, topic_id):
        raise AssertionError("this service test does not execute reads")


def test_create_topic_normalizes_input_and_author_identity():
    repo = FakeForumRepository()

    ForumService(repo).create_topic(
        author_type=" bot ",
        author_id=" bot-a ",
        client_request_id=" req-1 ",
        title="  Topic title  ",
        body="  Topic description  ",
    )

    assert repo.calls == [
        {
            "operation": "create_topic",
            "author_type": "BOT",
            "author_id": "bot-a",
            "client_request_id": "req-1",
            "title": "Topic title",
            "body": "Topic description",
            "topic_type": "DISCUSSION",
        }
    ]


def test_create_topic_normalizes_explicit_topic_type():
    repo = FakeForumRepository()

    ForumService(repo).create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-poll",
        title="Poll",
        body="Choose",
        topic_type=" poll ",
    )

    assert repo.calls[0]["topic_type"] == "POLL"


def test_create_reply_supports_human_author():
    repo = FakeForumRepository()

    ForumService(repo).create_reply(
        topic_id=" topic-1 ",
        author_type="human",
        author_id=" user-1 ",
        client_request_id=" req-2 ",
        body=" Reply ",
    )

    assert repo.calls == [
        {
            "operation": "create_reply",
            "topic_id": "topic-1",
            "author_type": "HUMAN",
            "author_id": "user-1",
            "client_request_id": "req-2",
            "body": "Reply",
        }
    ]


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        (
            {
                "author_type": "SYSTEM",
                "author_id": "bot-a",
                "client_request_id": "req",
                "title": "Topic",
                "body": "Description",
            },
            "author_type",
        ),
        (
            {
                "author_type": "BOT",
                "author_id": "  ",
                "client_request_id": "req",
                "title": "Topic",
                "body": "Description",
            },
            "author_id",
        ),
        (
            {
                "author_type": "BOT",
                "author_id": "bot-a",
                "client_request_id": "  ",
                "title": "Topic",
                "body": "Description",
            },
            "client_request_id",
        ),
        (
            {
                "author_type": "BOT",
                "author_id": "bot-a",
                "client_request_id": "req",
                "title": "  ",
                "body": "Description",
            },
            "title",
        ),
        (
            {
                "author_type": "BOT",
                "author_id": "bot-a",
                "client_request_id": "req",
                "title": "Topic",
                "body": "  ",
            },
            "body",
        ),
    ],
)
def test_create_topic_rejects_invalid_fields(kwargs, field):
    repo = FakeForumRepository()

    with pytest.raises(ValidationError) as excinfo:
        ForumService(repo).create_topic(**kwargs)

    assert field in excinfo.value.detail
    assert repo.calls == []


def test_list_topics_normalizes_filters_and_converts_page_to_offset():
    repo = FakeForumRepository()
    repo.list_topics = lambda **kwargs: (
        repo.calls.append({"operation": "list_topics", **kwargs})
        or ForumTopicPage(total=0, items=())
    )

    ForumService(repo).list_topics(
        keyword="  query  ", status=" closed ", page=3, page_size=10
    )

    assert repo.calls == [
        {
            "operation": "list_topics",
            "keyword": "query",
            "status": "CLOSED",
            "topic_type": None,
            "offset": 20,
            "limit": 10,
        }
    ]


def test_list_topics_normalizes_optional_topic_type_filter():
    repo = FakeForumRepository()
    repo.list_topics = lambda **kwargs: (
        repo.calls.append({"operation": "list_topics", **kwargs})
        or ForumTopicPage(total=0, items=())
    )

    ForumService(repo).list_topics(
        keyword=None, status=None, topic_type=" notice ", page=1, page_size=20
    )

    assert repo.calls[0]["topic_type"] == "NOTICE"


@pytest.mark.parametrize("topic_type", ["event", "POLL_OPTIONS"])
def test_rejects_unknown_topic_type(topic_type):
    repo = FakeForumRepository()

    with pytest.raises(ValidationError, match="topic_type"):
        ForumService(repo).create_topic(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req",
            title="Topic",
            body="Description",
            topic_type=topic_type,
        )


def test_get_topic_raises_not_found_for_invisible_topic():
    repo = FakeForumRepository()
    repo.get_topic = lambda topic_id: None

    with pytest.raises(NotFound, match="topic not found"):
        ForumService(repo).get_topic(topic_id=" missing ")


def test_list_posts_validates_pagination_before_repository_call():
    repo = FakeForumRepository()

    with pytest.raises(ValidationError, match="page_size"):
        ForumService(repo).list_posts(topic_id="topic-1", page=1, page_size=101)

    assert repo.calls == []
