from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.task.forum.models import (
    ForumHelpConfig,
    ForumPollConfig,
    ForumPollSelection,
    ForumPostRecord,
    ForumReplyCreateResult,
    ForumTopicAnalysisRecord,
    ForumTopicConfig,
    ForumTopicRecord,
)
from agentclaw.community.core.task.forum.services.forum_service import ForumService


class FakeForumRepository:
    def __init__(self, topic: ForumTopicRecord | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.topic = topic or _topic_record()

    def get_topic(self, topic_id):
        self.calls.append({"operation": "get_topic", "topic_id": topic_id})
        return self.topic

    def create_reply(self, **kwargs):
        self.calls.append({"operation": "create_reply", **kwargs})
        return ForumReplyCreateResult(post=_post_record(), created=True)


def _topic_record(*, topic_type: str = "DISCUSSION", topic_config=None) -> ForumTopicRecord:
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    return ForumTopicRecord(
        topic_id="topic-1",
        author_type="BOT",
        author_id="bot-a",
        title="Topic title",
        body="Topic description",
        status="OPEN",
        analysis=ForumTopicAnalysisRecord(),
        created_at=now,
        updated_at=now,
        topic_type=topic_type,
        topic_config=topic_config,
    )


def _post_record() -> ForumPostRecord:
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    return ForumPostRecord(
        post_id="post-1",
        topic_id="topic-1",
        author_type="BOT",
        author_id="bot-a",
        body="Reply",
        created_at=now,
        updated_at=now,
    )


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
        {"operation": "get_topic", "topic_id": "topic-1"},
        {
            "operation": "create_reply",
            "topic_id": "topic-1",
            "author_type": "HUMAN",
            "author_id": "user-1",
            "client_request_id": "req-2",
            "body": "Reply",
            "reply_to_post_id": None,
            "poll_selection": None,
            "author_display_name": None,
            "author_avatar_url": None,
        },
    ]


def test_create_reply_forwards_structured_poll_selection():
    repo = FakeForumRepository(
        topic=_topic_record(
            topic_type="POLL",
            topic_config=ForumTopicConfig(
                poll=ForumPollConfig(selection="MULTI", options=("A", "B"))
            ),
        )
    )
    selection = ForumPollSelection(option_indices=(0, 1))

    ForumService(repo).create_reply(
        topic_id="topic-1",
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-vote",
        body="I choose both options.",
        poll_selection=selection,
    )

    assert repo.calls[-1]["poll_selection"] == selection


def test_create_reply_rejects_floor_reply_for_poll_topic():
    repo = FakeForumRepository(
        topic=_topic_record(
            topic_type="POLL",
            topic_config=ForumTopicConfig(
                poll=ForumPollConfig(selection="SINGLE", options=("A", "B"))
            ),
        )
    )

    with pytest.raises(ValidationError, match="POLL replies must target the Topic"):
        ForumService(repo).create_reply(
            topic_id="topic-1",
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-vote",
            body="Floor votes are unsupported",
            reply_to_post_id="post-1",
            poll_selection=ForumPollSelection(option_indices=(0,)),
        )

    assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]


def test_create_reply_allows_floor_reply_for_share_topic():
    repo = FakeForumRepository(topic=_topic_record(topic_type="SHARE"))

    ForumService(repo).create_reply(
        topic_id="topic-1",
        author_type="HUMAN",
        author_id="user-1",
        client_request_id="req-share-floor",
        body="Share nested reply",
        reply_to_post_id="post-1",
    )

    assert repo.calls[-1]["reply_to_post_id"] == "post-1"


@pytest.mark.parametrize(
    ("selection_rule", "option_indices", "message"),
    [
        ("SINGLE", (0, 1), "exactly one option"),
        ("SINGLE", (2,), "invalid option index"),
        ("MULTI", (0, 0), "must not contain duplicates"),
        ("MULTI", (), "at least one option"),
    ],
)
def test_create_reply_rejects_invalid_poll_selection(
    selection_rule, option_indices, message
):
    repo = FakeForumRepository(
        topic=_topic_record(
            topic_type="POLL",
            topic_config=ForumTopicConfig(
                poll=ForumPollConfig(
                    selection=selection_rule,
                    options=("A", "B"),
                )
            ),
        )
    )

    with pytest.raises(ValidationError, match=message):
        ForumService(repo).create_reply(
            topic_id="topic-1",
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-vote",
            body="Vote",
            poll_selection=ForumPollSelection(option_indices=option_indices),
        )

    assert repo.calls[-1] == {"operation": "get_topic", "topic_id": "topic-1"}


def test_create_reply_rejects_poll_selection_for_non_poll_topic():
    repo = FakeForumRepository(topic=_topic_record(topic_type="DISCUSSION"))

    with pytest.raises(ValidationError, match="poll_selection is only allowed"):
        ForumService(repo).create_reply(
            topic_id="topic-1",
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-vote",
            body="Discussion reply",
            poll_selection=ForumPollSelection(option_indices=(0,)),
        )

    assert repo.calls[-1] == {"operation": "get_topic", "topic_id": "topic-1"}


def test_create_reply_requires_selection_for_poll_topic():
    repo = FakeForumRepository(
        topic=_topic_record(
            topic_type="POLL",
            topic_config=ForumTopicConfig(
                poll=ForumPollConfig(selection="SINGLE", options=("A", "B"))
            ),
        )
    )

    with pytest.raises(ValidationError, match="poll_selection is required"):
        ForumService(repo).create_reply(
            topic_id="topic-1",
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-vote",
            body="Choose one",
        )

    assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]


def test_create_reply_normalizes_optional_reference():
    repo = FakeForumRepository()

    ForumService(repo).create_reply(
        topic_id="topic-1",
        author_type="HUMAN",
        author_id="user-1",
        client_request_id="req-referenced",
        body="Reply",
        reply_to_post_id=" post-1 ",
    )

    assert repo.calls[-1]["reply_to_post_id"] == "post-1"


def test_create_reply_rejects_floor_reply_for_notice_topic():
    repo = FakeForumRepository(topic=_topic_record(topic_type="NOTICE"))

    with pytest.raises(ValidationError, match="NOTICE replies must target the Topic"):
        ForumService(repo).create_reply(
            topic_id="topic-1",
            author_type="HUMAN",
            author_id="user-1",
            client_request_id="req-notice-floor",
            body="Notice nested reply",
            reply_to_post_id="post-1",
        )

    assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]


def test_create_reply_rejects_floor_reply_for_help_topic():
    topic = _topic_record(
        topic_type="HELP",
        topic_config=ForumTopicConfig(
            help=ForumHelpConfig(expected_time="今天"),
        ),
    )
    repo = FakeForumRepository(topic)

    with pytest.raises(ValidationError, match="HELP replies must target the Topic"):
        ForumService(repo).create_reply(
            topic_id="topic-1",
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-help",
            body="HELP can only be a topic reply",
            reply_to_post_id="post-1",
        )
    assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]
