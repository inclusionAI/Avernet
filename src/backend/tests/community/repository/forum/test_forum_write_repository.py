from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.errors import Conflict, NotFound
from agentclaw.community.core.forum.models import (
    TOPIC_STATUS_CLOSED,
    TOPIC_STATUS_LOCKED,
)
from agentclaw.community.core.forum.repository.models import (
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.core.repository.implementations.forum.forum_repository import (
    ForumRepository,
    _is_post_request_idempotency_conflict,
    _is_topic_request_idempotency_conflict,
)


def _create_topic(
    repo,
    *,
    author_type="BOT",
    author_id="bot-a",
    request_id="req-topic",
):
    return repo.create_topic(
        author_type=author_type,
        author_id=author_id,
        client_request_id=request_id,
        title="Topic title",
        body="Topic description",
    )


def _create_reply(
    repo,
    topic_id,
    *,
    author_type="BOT",
    author_id="bot-b",
    request_id="req-reply-1",
    body="First reply",
):
    return repo.create_reply(
        topic_id=topic_id,
        author_type=author_type,
        author_id=author_id,
        client_request_id=request_id,
        body=body,
    )


def test_create_topic_persists_topic_description_without_root_post(db):
    repo = ForumRepository(db)

    result = _create_topic(repo)

    assert result.created is True
    assert result.topic.topic_id.startswith("topic_")
    assert result.topic.author_type == "BOT"
    assert result.topic.author_id == "bot-a"
    assert result.topic.title == "Topic title"
    assert result.topic.body == "Topic description"
    assert result.topic.status == "OPEN"
    with db.orm_session() as session:
        assert session.query(ForumPostModel).count() == 0


def test_create_topic_is_idempotent_without_comparing_content(db):
    repo = ForumRepository(db)
    first = _create_topic(repo)
    second = repo.create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-topic",
        title="Different title",
        body="Different description",
    )

    assert second.created is False
    assert second.topic.topic_id == first.topic.topic_id
    assert second.topic.title == "Topic title"
    assert second.topic.body == "Topic description"


def test_topic_idempotency_is_scoped_by_author_identity(db):
    repo = ForumRepository(db)

    bot_topic = _create_topic(repo, request_id="shared-request")
    human_topic = _create_topic(
        repo,
        author_type="HUMAN",
        author_id="user-a",
        request_id="shared-request",
    )

    assert bot_topic.topic.topic_id != human_topic.topic.topic_id


def test_create_reply_persists_author_without_tenant_or_environment_columns(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic

    result = _create_reply(
        repo,
        topic.topic_id,
        author_type="HUMAN",
        author_id="user-b",
    )

    assert result.created is True
    assert result.post.topic_id == topic.topic_id
    assert result.post.author_type == "HUMAN"
    assert result.post.author_id == "user-b"
    assert "env" not in ForumPostModel.__table__.columns
    assert "avernet_tenant" not in ForumPostModel.__table__.columns


def test_create_reply_is_idempotent_per_topic_author_and_request(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic

    first = _create_reply(repo, topic.topic_id)
    second = _create_reply(
        repo,
        topic.topic_id,
        body="Different reply body",
    )

    assert second.created is False
    assert second.post.post_id == first.post.post_id
    assert second.post.body == "First reply"
    with db.orm_session() as session:
        assert session.query(ForumPostModel).count() == 1


def test_same_reply_request_id_can_be_used_in_another_topic(db):
    repo = ForumRepository(db)
    first_topic = _create_topic(repo, request_id="topic-1").topic
    second_topic = _create_topic(repo, request_id="topic-2").topic

    first = _create_reply(repo, first_topic.topic_id, request_id="shared-reply")
    second = _create_reply(repo, second_topic.topic_id, request_id="shared-reply")

    assert first.post.post_id != second.post.post_id


def test_same_content_can_be_replied_more_than_once_with_different_request_ids(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic

    first = _create_reply(repo, topic.topic_id, request_id="reply-1", body="same")
    second = _create_reply(repo, topic.topic_id, request_id="reply-2", body="same")

    assert first.post.post_id != second.post.post_id


def test_create_reply_rejects_unknown_topic(db):
    repo = ForumRepository(db)

    with pytest.raises(NotFound):
        _create_reply(repo, "missing-topic")


@pytest.mark.parametrize("status", [TOPIC_STATUS_CLOSED, TOPIC_STATUS_LOCKED])
def test_create_reply_rejects_non_open_topic(db, status):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic
    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).one()
        row.status = status

    with pytest.raises(Conflict, match=status.lower()):
        _create_reply(repo, topic.topic_id)


def test_idempotent_reply_replay_still_succeeds_after_topic_is_locked(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic
    first = _create_reply(repo, topic.topic_id)
    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).one()
        row.status = TOPIC_STATUS_LOCKED

    replay = _create_reply(repo, topic.topic_id, body="ignored")

    assert replay.created is False
    assert replay.post.post_id == first.post.post_id


def test_idempotency_classifiers_only_match_their_request_unique_keys():
    topic_request = IntegrityError(
        "INSERT", {}, Exception("Duplicate entry for key 'uk_forum_topic_request'")
    )
    post_request = IntegrityError(
        "INSERT", {}, Exception("Duplicate entry for key 'uk_forum_post_request'")
    )
    post_id = IntegrityError(
        "INSERT", {}, Exception("Duplicate entry for key 'uk_forum_post_id'")
    )

    assert _is_topic_request_idempotency_conflict(topic_request) is True
    assert _is_topic_request_idempotency_conflict(post_id) is False
    assert _is_post_request_idempotency_conflict(post_request) is True
    assert _is_post_request_idempotency_conflict(post_id) is False
