from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.forum.models import (
    ForumPostRecord,
    ForumReplyCreateResult,
    ForumTopicAnalysisRecord,
    ForumTopicRecord,
)
from agentclaw.community.core.task.forum.services.notifying_forum_service import (
    NotifyingForumService,
)
from agentclaw.community.core.work_orders.models import WorkOrderEventType


_NOW = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


@dataclass
class FakeNotifications:
    notices: list[dict[str, Any]]

    def notify(self, **kwargs):
        self.notices.append(dict(kwargs))


class FakeForumRepository:
    def __init__(self, *, topic=None, parent=None, created=True) -> None:
        self.topic = topic
        self.parent = parent
        self.created = created

    def create_reply(self, **kwargs):
        return ForumReplyCreateResult(
            post=_post(
                topic_id=kwargs["topic_id"],
                author_type=kwargs["author_type"],
                author_id=kwargs["author_id"],
                reply_to_post_id=kwargs.get("reply_to_post_id"),
            ),
            created=self.created,
        )

    def get_topic(self, topic_id: str):
        return self.topic

    def get_post(self, *, topic_id: str, post_id: str):
        return self.parent


def _topic(*, topic_type: str = "DISCUSSION") -> ForumTopicRecord:
    return ForumTopicRecord(
        topic_id="topic-1",
        author_type="HUMAN",
        author_id="user-a",
        title="Topic",
        body="Body",
        status="OPEN",
        analysis=ForumTopicAnalysisRecord(),
        created_at=_NOW,
        updated_at=_NOW,
        topic_type=topic_type,
    )


def _post(
    *,
    post_id: str = "post-created",
    topic_id: str = "topic-1",
    author_type: str = "HUMAN",
    author_id: str = "user-b",
    reply_to_post_id: str | None = None,
) -> ForumPostRecord:
    return ForumPostRecord(
        post_id=post_id,
        topic_id=topic_id,
        author_type=author_type,
        author_id=author_id,
        body="Reply body",
        display_name="Reply author",
        reply_to_post_id=reply_to_post_id,
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_notifying_forum_service_emits_reply_and_help_response_notices() -> None:
    notifications = FakeNotifications(notices=[])
    reply_repository = FakeForumRepository(topic=_topic(topic_type="DISCUSSION"))
    help_repository = FakeForumRepository(topic=_topic(topic_type="HELP"))

    for repository, expected_event, expected_text in (
        (reply_repository, WorkOrderEventType.BBS_REPLIED, "回复了你的帖子"),
        (help_repository, WorkOrderEventType.BBS_HELP_RESPONDED, "响应了你的求助帖"),
    ):
        NotifyingForumService(repository, notifications).create_reply(
            topic_id="topic-1",
            author_type="HUMAN",
            author_id="user-b",
            client_request_id="request-id",
            body="Reply",
        )

    assert [notice["event_type"] for notice in notifications.notices] == [
        WorkOrderEventType.BBS_REPLIED,
        WorkOrderEventType.BBS_HELP_RESPONDED,
    ]
    assert [notice["text"] for notice in notifications.notices] == [
        "回复了你的帖子",
        "响应了你的求助帖",
    ]
    assert notifications.notices[0]["recipient_id"] == "user-a"
    assert notifications.notices[0]["actor_display_name"] == "Reply author"


def test_notifying_forum_service_composes_nested_reply_notice() -> None:
    notifications = FakeNotifications(notices=[])
    repository = FakeForumRepository(topic=_topic(), parent=_post(post_id="post-parent"))

    NotifyingForumService(repository, notifications).create_reply(
        topic_id="topic-1",
        author_type="HUMAN",
        author_id="user-b",
        client_request_id="request-id",
        body="Nested reply",
        reply_to_post_id="post-parent",
    )

    assert len(notifications.notices) == 1
    notice = notifications.notices[0]
    assert notice["event_type"] == WorkOrderEventType.BBS_REPLIED
    assert notice["text"] == "回复了你的回复"
    assert notice["recipient_id"] == "user-b"


def test_notifying_forum_service_ignores_idempotent_replay() -> None:
    notifications = FakeNotifications(notices=[])
    repository = FakeForumRepository(topic=_topic(), created=False)

    NotifyingForumService(repository, notifications).create_reply(
        topic_id="topic-1",
        author_type="HUMAN",
        author_id="user-b",
        client_request_id="request-id",
        body="Reply",
    )

    assert notifications.notices == []


def test_notifying_forum_service_propagates_missing_topic_or_parent() -> None:
    repository = FakeForumRepository(topic=None)
    notifications = FakeNotifications(notices=[])

    with pytest.raises(NotFound, match="topic not found"):
        NotifyingForumService(repository, notifications).create_reply(
            topic_id="topic-missing",
            author_type="HUMAN",
            author_id="user-b",
            client_request_id="request-id",
            body="Reply",
        )

    repository = FakeForumRepository(topic=_topic(), parent=None)

    with pytest.raises(NotFound, match="reply target not found in this topic"):
        NotifyingForumService(repository, notifications).create_reply(
            topic_id="topic-1",
            author_type="HUMAN",
            author_id="user-b",
            client_request_id="request-id",
            body="Nested reply",
            reply_to_post_id="post-missing",
        )

    assert notifications.notices == []
