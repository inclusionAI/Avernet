from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import Conflict, NotFound, ValidationError
from agentclaw.community.core.task.forum.models import (
    ForumPollConfig,
    ForumPollSelection,
    ForumTopicAnalysisRecord,
    ForumTopicConfig,
    ForumPostRecord,
    ForumTopicPage,
    ForumReplyCreateResult,
    ForumShareConfig,
    ForumTopicCreateResult,
    ForumTopicRecord,
    ForumTopicViewerRecord,
)
from agentclaw.community.core.task.forum.services.forum_service import ForumService


class FakeForumRepository:
    def __init__(self, topic=None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.topic = topic
        self._default_topic = _topic_record()

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
                analysis=ForumTopicAnalysisRecord(),
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
        self.calls.append({"operation": "get_topic", "topic_id": topic_id})
        return self._default_topic if self.topic is None else self.topic

    def get_topic_viewer_state(
        self, *, topic_id: str, author_type: str, author_id: str
    ):
        self.calls.append(
            {
                "operation": "get_topic_viewer_state",
                "topic_id": topic_id,
                "author_type": author_type,
                "author_id": author_id,
            }
        )
        return ForumTopicViewerRecord(
            has_voted=True,
            vote_post_id="post-vote",
            poll_selection=ForumPollSelection(option_indices=(0,)),
        )


def _topic_record(
    topic_type="DISCUSSION",
    topic_config=None,
):
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
            "topic_config": None,
            "author_display_name": None,
            "author_avatar_url": None,
        }
    ]


def test_create_topic_normalizes_explicit_notice_topic_type():
    repo = FakeForumRepository()

    ForumService(repo).create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-notice",
        title="Notice",
        body="Announcement",
        topic_type=" notice ",
    )

    assert repo.calls[0]["topic_type"] == "NOTICE"


def test_create_topic_normalizes_and_forwards_topic_config():
    repo = FakeForumRepository()
    deadline = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    ForumService(repo).create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-poll",
        title="Poll",
        body="Choose options",
        topic_type=" poll ",
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(
                selection=" single ",
                options=(" Option A ", "Option B"),
                deadline_at=deadline,
            )
        ),
    )

    assert repo.calls[0]["topic_type"] == "POLL"
    assert repo.calls[0]["topic_config"] == ForumTopicConfig(
        poll=ForumPollConfig(
            selection="SINGLE",
            options=("Option A", "Option B"),
            deadline_at=deadline,
        )
    )


def test_create_share_topic_allows_empty_attachment_list():
    repo = FakeForumRepository()
    config = ForumTopicConfig(share=ForumShareConfig(attachment_ids=()))

    ForumService(repo).create_topic(
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-share",
        title="Share",
        body="Experience without files",
        topic_type="SHARE",
        topic_config=config,
    )

    assert repo.calls[0]["topic_config"] == config


def test_create_topic_requires_topic_config_for_poll_topic():
    repo = FakeForumRepository()

    with pytest.raises(ValidationError, match="topic_config.poll is required"):
        ForumService(repo).create_topic(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-poll",
            title="Poll",
            body="Choose",
            topic_type="POLL",
            topic_config=None,
        )

    assert repo.calls == []


def test_create_topic_requires_poll_section_for_poll_topic():
    repo = FakeForumRepository()

    with pytest.raises(ValidationError, match="topic_config.poll is required"):
        ForumService(repo).create_topic(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-poll",
            title="Poll",
            body="Choose",
            topic_type="POLL",
            topic_config=ForumTopicConfig(),
        )

    assert repo.calls == []


def test_create_topic_rejects_topic_config_for_non_poll_topic():
    repo = FakeForumRepository()
    topic_config = ForumTopicConfig(
        poll=ForumPollConfig(selection="SINGLE", options=("A", "B"))
    )

    with pytest.raises(ValidationError, match="topic_config is only allowed"):
        ForumService(repo).create_topic(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-discussion",
            title="Discussion",
            body="Description",
            topic_type="DISCUSSION",
            topic_config=topic_config,
        )

    assert repo.calls == []


@pytest.mark.parametrize(
    "options",
    [
        ("A",),
        ("A", "B") * 11,
        ("A", ""),
        ("A", "B", "A"),
    ],
)
def test_create_topic_rejects_invalid_poll_options(options):
    repo = FakeForumRepository()
    topic_config = ForumTopicConfig(
        poll=ForumPollConfig(selection="SINGLE", options=options)
    )

    with pytest.raises(ValidationError, match="poll.options"):
        ForumService(repo).create_topic(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-poll",
            title="Poll",
            body="Choose",
            topic_type="POLL",
            topic_config=topic_config,
        )

    assert repo.calls == []


def test_create_topic_rejects_naive_poll_deadline():
    repo = FakeForumRepository()
    topic_config = ForumTopicConfig(
        poll=ForumPollConfig(
            selection="SINGLE",
            options=("A", "B"),
            deadline_at=datetime(2026, 10, 20, 12, 0),
        )
    )

    with pytest.raises(ValidationError, match="poll.deadline_at"):
        ForumService(repo).create_topic(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-poll",
            title="Poll",
            body="Choose",
            topic_type="POLL",
            topic_config=topic_config,
        )

    assert repo.calls == []


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
            "author_id": None,
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


def test_get_topic_attaches_viewer_state_when_viewer_is_declared():
    repo = FakeForumRepository()
    viewer = ForumTopicViewerRecord(
        has_voted=True,
        vote_post_id="post-vote",
        poll_selection=ForumPollSelection(option_indices=(0,)),
    )

    result = ForumService(repo).get_topic(
        topic_id=" topic-1 ",
        viewer_author_type=" human ",
        viewer_author_id=" user-1 ",
    )

    assert result.viewer == viewer
    assert repo.calls[-1] == {
        "operation": "get_topic_viewer_state",
        "topic_id": "topic-1",
        "author_type": "HUMAN",
        "author_id": "user-1",
    }


def test_get_topic_requires_viewer_type_and_viewer_id_to_be_declared_together():
    repo = FakeForumRepository()

    with pytest.raises(ValidationError, match="viewer_author_type and viewer_author_id"):
        ForumService(repo).get_topic(
            topic_id="topic-1", viewer_author_type="HUMAN", viewer_author_id=None
        )

    assert repo.calls == []


def test_list_posts_validates_pagination_before_repository_call():
    repo = FakeForumRepository()

    with pytest.raises(ValidationError, match="page_size"):
        ForumService(repo).list_posts(topic_id="topic-1", page=1, page_size=101)

    assert repo.calls == []


# ---------------------------------------------------------------------------
# close_topic
# ---------------------------------------------------------------------------
# A focused repository double for the close test: ``get_topic`` returns a
# configurable record and ``update_topic_status`` records the requested status
# and returns the refreshed record (or ``None`` to simulate a concurrent
# delete race). The closure persists ``call_record`` so the tests can assert
# the service never reaches the repo writer when it should not.


def _close_test_topic(
    *,
    topic_id: str = "topic-close",
    status: str = "OPEN",
    author_type: str = "HUMAN",
    author_id: str = "user-a",
) -> ForumTopicRecord:
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    return ForumTopicRecord(
        topic_id=topic_id,
        author_type=author_type,
        author_id=author_id,
        title="Topic title",
        body="Topic description",
        status=status,
        analysis=ForumTopicAnalysisRecord(),
        created_at=now,
        updated_at=now,
    )


_CLOSE_WRITER_UNSET = object()


def _close_test_repo(topic, *, writer_returns=_CLOSE_WRITER_UNSET):
    captured: list[dict] = []

    def update_topic_status(*, topic_id, status):
        captured.append({"op": "update_topic_status", "topic_id": topic_id, "status": status})
        if writer_returns is _CLOSE_WRITER_UNSET:
            if topic is None:
                return None
            return dataclasses.replace(topic, status=status)
        return writer_returns

    repo = FakeForumRepository()
    if topic is not None:
        repo.get_topic = lambda topic_id=topic.topic_id: topic if topic_id == topic.topic_id else topic
    else:
        repo.get_topic = lambda topic_id=None: topic
    repo.update_topic_status = update_topic_status  # type: ignore[assignment]
    repo._close_captured = captured  # type: ignore[attr-defined]
    return repo


def test_close_topic_closes_open_topic_via_repository_writer():
    topic = _close_test_topic(status="OPEN")
    repo = _close_test_repo(topic)

    result = ForumService(repo).close_topic(topic_id=topic.topic_id)

    assert result.status == "CLOSED"
    assert repo._close_captured == [
        {"op": "update_topic_status", "topic_id": "topic-close", "status": "CLOSED"}
    ]


def test_close_topic_is_idempotent_for_already_closed_topic():
    topic = _close_test_topic(status="CLOSED")
    repo = _close_test_repo(topic)

    result = ForumService(repo).close_topic(topic_id=topic.topic_id)

    assert result.status == "CLOSED"
    # Already closed: no writer call.
    assert repo._close_captured == []


def test_close_topic_refuses_locked_topic_with_conflict():
    topic = _close_test_topic(status="LOCKED")
    repo = _close_test_repo(topic)

    with pytest.raises(Conflict, match="LOCKED"):
        ForumService(repo).close_topic(topic_id=topic.topic_id)

    assert repo._close_captured == []


def test_close_topic_raises_not_found_when_topic_missing():
    repo = _close_test_repo(None)

    with pytest.raises(NotFound, match="topic not found"):
        ForumService(repo).close_topic(topic_id="missing")

    assert repo._close_captured == []


def test_close_topic_raises_not_found_when_writer_finds_no_row_after_read():
    # The read returned a topic, but the writer raced a concurrent delete and
    # returned None — the service maps that back to NotFound rather than
    # answering a 200 with a None record.
    topic = _close_test_topic(status="OPEN")
    repo = _close_test_repo(topic, writer_returns=None)

    with pytest.raises(NotFound, match="topic not found"):
        ForumService(repo).close_topic(topic_id=topic.topic_id)


def test_close_topic_normalizes_topic_id_before_lookup():
    topic = _close_test_topic(status="OPEN")
    repo = _close_test_repo(topic)
    repo.get_topic = lambda topic_id=None: topic if topic_id == "topic-close" else None

    result = ForumService(repo).close_topic(topic_id=" topic-close ")

    assert result.status == "CLOSED"
    assert repo._close_captured == [
        {"op": "update_topic_status", "topic_id": "topic-close", "status": "CLOSED"}
    ]


def test_close_topic_rejects_empty_topic_id():
    repo = _close_test_repo(_close_test_topic())

    with pytest.raises(ValidationError, match="topic_id"):
        ForumService(repo).close_topic(topic_id="   ")
