from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.errors import Conflict, Forbidden
from agentclaw.community.core.task.forum.models import (
    ForumTopicAnalysisRecord,
    ForumTopicRecord,
)
from agentclaw.community.core.task.forum.services.forum_service import ForumService


class FakeTopicContentRepository:
    def __init__(self, topic: ForumTopicRecord) -> None:
        self.calls: list[dict[str, Any]] = []
        self.topic = topic

    def get_topic(self, topic_id: str):
        self.calls.append({"operation": "get_topic", "topic_id": topic_id})
        return self.topic

    def update_topic_content(
        self,
        *,
        topic_id: str,
        title: str,
        body: str,
        expected_status: str,
    ):
        self.calls.append(
            {
                "operation": "update_topic_content",
                "topic_id": topic_id,
                "title": title,
                "body": body,
                "expected_status": expected_status,
            }
        )
        return dataclasses.replace(self.topic, title=title, body=body)

    def delete_topic(self, topic_id: str):
        self.calls.append({"operation": "delete_topic", "topic_id": topic_id})
        return True


def _topic_record(
    *,
    author_type: str = "BOT",
    author_id: str = "bot-a",
    status: str = "OPEN",
) -> ForumTopicRecord:
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    return ForumTopicRecord(
        topic_id="topic-1",
        author_type=author_type,
        author_id=author_id,
        title="Topic title",
        body="Topic description",
        status=status,
        analysis=ForumTopicAnalysisRecord(),
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# update_topic / delete_topic — Topic only; replies are intentionally read-only
# ---------------------------------------------------------------------------


def test_update_topic_normalizes_input_and_updates_own_open_topic():
    topic = _topic_record(author_type="HUMAN", author_id="user-a")
    repo = FakeTopicContentRepository(topic)

    result = ForumService(repo).update_topic(
        topic_id=" topic-1 ",
        author_type=" human ",
        author_id=" user-a ",
        title="  Edited title  ",
        body="  Edited body  ",
    )

    assert (result.title, result.body) == ("Edited title", "Edited body")
    assert repo.calls == [
        {"operation": "get_topic", "topic_id": "topic-1"},
        {
            "operation": "update_topic_content",
            "topic_id": "topic-1",
            "title": "Edited title",
            "body": "Edited body",
            "expected_status": "OPEN",
        },
    ]


def test_update_topic_refuses_non_author():
    repo = FakeTopicContentRepository(_topic_record(author_type="HUMAN", author_id="user-a"))

    with pytest.raises(Forbidden, match="only the topic author may edit"):
        ForumService(repo).update_topic(
            topic_id="topic-1",
            author_type="HUMAN",
            author_id="user-b",
            title="Edited title",
            body="Edited body",
        )

    assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]


def test_update_topic_refuses_closed_or_locked_topic():
    for status in ("CLOSED", "LOCKED"):
        repo = FakeTopicContentRepository(
            _topic_record(author_type="HUMAN", author_id="user-a", status=status)
        )
        with pytest.raises(Conflict, match="cannot be edited"):
            ForumService(repo).update_topic(
                topic_id="topic-1",
                author_type="HUMAN",
                author_id="user-a",
                title="Edited title",
                body="Edited body",
            )
        assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]


def test_update_topic_maps_writer_status_race_to_conflict():
    topic = _topic_record(author_type="HUMAN", author_id="user-a")
    repo = FakeTopicContentRepository(topic)
    repo.update_topic_content = lambda **_kwargs: None  # type: ignore[assignment]

    with pytest.raises(Conflict, match="cannot be edited"):
        ForumService(repo).update_topic(
            topic_id=topic.topic_id,
            author_type="HUMAN",
            author_id="user-a",
            title="Edited title",
            body="Edited body",
        )


def test_delete_topic_deletes_own_non_locked_topic():
    topic = _topic_record(author_type="HUMAN", author_id="user-a")
    repo = FakeTopicContentRepository(topic)

    assert ForumService(repo).delete_topic(
        topic_id=" topic-1 ",
        author_type=" human ",
        author_id=" user-a ",
    ) is True
    assert repo.calls == [
        {"operation": "get_topic", "topic_id": "topic-1"},
        {"operation": "delete_topic", "topic_id": "topic-1"},
    ]


def test_delete_topic_refuses_non_author_and_locked_topic():
    repo = FakeTopicContentRepository(_topic_record(author_type="HUMAN", author_id="user-a"))
    with pytest.raises(Forbidden, match="only the topic author may delete"):
        ForumService(repo).delete_topic(
            topic_id="topic-1",
            author_type="HUMAN",
            author_id="user-b",
        )
    assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]

    repo = FakeTopicContentRepository(
        _topic_record(author_type="HUMAN", author_id="user-a", status="LOCKED")
    )
    with pytest.raises(Conflict, match="LOCKED"):
        ForumService(repo).delete_topic(
            topic_id="topic-1",
            author_type="HUMAN",
            author_id="user-a",
        )
    assert repo.calls == [{"operation": "get_topic", "topic_id": "topic-1"}]


def test_delete_topic_maps_writer_status_race_to_conflict():
    topic = _topic_record(author_type="HUMAN", author_id="user-a")
    repo = FakeTopicContentRepository(topic)
    repo.delete_topic = lambda _topic_id: False  # type: ignore[assignment]

    with pytest.raises(Conflict, match="cannot be deleted"):
        ForumService(repo).delete_topic(
            topic_id=topic.topic_id,
            author_type="HUMAN",
            author_id="user-a",
        )
