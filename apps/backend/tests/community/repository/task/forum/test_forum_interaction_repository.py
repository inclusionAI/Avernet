"""Repository tests for append-only BBS interaction events and summaries."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    ForumRepository,
)
from agentclaw.community.core.repository.implementations.task.forum.forum_interaction_repository import (
    ForumInteractionRepository,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorTargetRef,
    ForumActorType,
    ForumInteractionActionType,
    ForumInteractionTargetType,
    ForumTargetRef,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumInteractionModel,
    ForumPostModel,
)
from agentclaw.community.core.task.forum.services.forum_service import ForumService


class FailingDatabase:
    @contextmanager
    def orm_session(self):
        raise RuntimeError("database unavailable")
        yield None


def _events(repo, *, actor_type="HUMAN", actor_id="user-a"):
    def add(action, target_type, target_id):
        repo.insert_interaction(
            actor_type=actor_type,
            actor_id=actor_id,
            action_type=action,
            target_type=target_type,
            target_id=target_id,
        )

    return add


def test_interaction_repository_appends_repeated_actor_target_events(db):
    repo = ForumInteractionRepository(db)

    first = repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
    )
    second = repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
    )

    assert first.actor_id == second.actor_id == "user-a"
    with db.orm_session() as session:
        rows = (
            session.query(
                ForumInteractionModel.action_type,
                ForumInteractionModel.target_id,
            )
            .order_by(ForumInteractionModel.id)
            .all()
        )
    assert len(rows) == 2
    assert [(row.action_type, row.target_id) for row in rows] == [
        ("LIKE", "topic-1"),
        ("LIKE", "topic-1"),
    ]


def test_interaction_record_fields_support_http_response_projection(db):
    from agentclaw.community.adapters.http.openapi_v1.task.bbs.interaction_schemas import (
        InteractionRecorded,
    )

    repo = ForumInteractionRepository(db)
    record = repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="VIEW",
        target_type="TOPIC",
        target_id="topic-1",
    )

    assert record.actor_type is ForumActorType.HUMAN
    assert record.action_type is ForumInteractionActionType.VIEW
    assert record.target_type is ForumInteractionTargetType.TOPIC
    response = InteractionRecorded.from_record(record)
    assert (response.actor_type, response.action_type, response.target_type) == (
        "HUMAN",
        "VIEW",
        "TOPIC",
    )


def test_summary_dimensions_and_topic_projections_do_not_share_counts(db):
    content_repo = ForumRepository(db)
    topic = content_repo.create_topic(
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-topic",
        title="Topic",
        body="Body",
        topic_type="HELP",
    ).topic
    first = content_repo.create_reply(
        topic_id=topic.topic_id,
        author_type="HUMAN",
        author_id="user-b",
        client_request_id="req-first",
        body="First",
    ).post
    content_repo.create_reply(
        topic_id=topic.topic_id,
        author_type="BOT",
        author_id="bot-b",
        client_request_id="req-second",
        body="Second",
    ).post

    repo = ForumInteractionRepository(db)
    _events(repo)("VIEW", "TOPIC", topic.topic_id)
    _events(repo)("VIEW", "TOPIC", topic.topic_id)
    _events(repo)("LIKE", "TOPIC", topic.topic_id)
    _events(repo)("FAVORITE", "POST", first.post_id)
    _events(repo, actor_type="HUMAN", actor_id="user-b")("FOLLOW", "HUMAN", "user-a")
    _events(repo, actor_type="HUMAN", actor_id="user-b")(
        "FOLLOW", "TOPIC", topic.topic_id
    )

    target = repo.get_summary(target_type="TOPIC", target_id=topic.topic_id)
    actor = repo.get_summary(actor_type="HUMAN", actor_id="user-a")
    pair = repo.get_summary(
        actor_type="HUMAN",
        actor_id="user-a",
        target_type="TOPIC",
        target_id=topic.topic_id,
    )

    assert (target.view_count, target.like_count, target.follow_count) == (2, 1, 1)
    assert target.reply_participant_count == 2
    assert target.helpful_reply_count == 0
    assert actor.follow_count == 1
    assert (actor.view_count, actor.like_count, actor.favorite_count) == (0, 0, 0)
    assert (pair.view_count, pair.like_count, pair.follow_count) == (2, 1, 0)
    assert (pair.favorite_count, pair.topic_count, pair.reply_count) == (0, 0, 0)


def test_batch_summaries_group_unique_dimensions_in_one_result_map(db):
    repo = ForumInteractionRepository(db)
    _events(repo)("LIKE", "TOPIC", "topic-1")
    _events(repo, actor_id="user-b")("FOLLOW", "HUMAN", "user-a")
    actor = ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id="topic-1")
    pair = ForumActorTargetRef(actor=actor, target=target)

    actor_stats = repo.batch_get_actor_summaries((actor,))
    target_stats = repo.batch_get_target_summaries((target,))
    pair_stats = repo.batch_get_actor_target_summaries((pair,))

    assert actor_stats[actor].follow_count == 1
    assert target_stats[target].like_count == 1
    assert pair_stats[pair].like_count == 1


def test_batch_actor_summaries_count_follows_received_by_identity(db):
    repo = ForumInteractionRepository(db)
    _events(repo, actor_id="user-a")("FOLLOW", "HUMAN", "user-a")
    _events(repo, actor_id="user-b")("FOLLOW", "HUMAN", "user-a")
    user_a = ForumActorRef(type=ForumActorType.HUMAN, id="user-a")

    stats = repo.batch_get_actor_summaries((user_a,))

    assert stats[user_a].follow_count == 2


def test_interaction_summaries_are_isolated_by_environment(db, monkeypatch):
    repo = ForumInteractionRepository(db)
    _events(repo)("VIEW", "TOPIC", "topic-1")

    monkeypatch.setenv("SERVER_ENV", "pre")
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id="topic-1")
    assert repo.batch_get_target_summaries((target,))[target].view_count == 0


def test_database_failure_propagates_and_never_returns_a_summary():
    repo = ForumInteractionRepository(FailingDatabase())
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id="topic-1")

    with pytest.raises(RuntimeError, match="database unavailable"):
        repo.batch_get_target_summaries((target,))


def test_interaction_ddl_and_orm_contract_stay_additive_and_unique_free():
    ddl_path = Path(
        "src/agentclaw/community/core/task/forum/sql/2026_10_27_forum_interaction.sql"
    )
    ddl = ddl_path.read_text()

    assert "ac_forum_interaction" in ddl
    assert "UNIQUE KEY" not in ddl
    assert "uk_forum_interaction" not in ddl.upper()
    assert "avernet_tenant" not in ddl
    assert "active" not in ddl
    assert "dedupe_date" not in ddl
    assert "COLLATE utf8mb4_bin" in ddl
    columns = {column.name for column in ForumInteractionModel.__table__.columns}
    assert columns == {
        "id",
        "actor_type",
        "actor_id",
        "action_type",
        "target_type",
        "target_id",
        "env",
        "gmt_create",
        "gmt_modified",
    }
    indexes = {index.name for index in ForumInteractionModel.__table__.indexes}
    assert indexes == {
        "idx_forum_interaction_target",
        "idx_forum_interaction_actor",
    }
    assert not any(index.unique for index in ForumInteractionModel.__table__.indexes)


def test_helpful_accept_transaction_rolls_back_state_and_event(db, monkeypatch):
    content_repo = ForumRepository(db)
    topic = content_repo.create_topic(
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-help-topic",
        title="Help",
        body="Help",
        topic_type="HELP",
    ).topic
    post = content_repo.create_reply(
        topic_id=topic.topic_id,
        author_type="HUMAN",
        author_id="user-b",
        client_request_id="req-help-post",
        body="Answer",
    ).post

    def fail_to_record(self):
        raise RuntimeError("event projection failed")

    monkeypatch.setattr(ForumPostModel, "to_record", fail_to_record)
    with pytest.raises(RuntimeError, match="event projection failed"):
        content_repo.update_post_helpful_with_accept(
            topic_id=topic.topic_id,
            post_id=post.post_id,
            helpful=True,
            actor_type="HUMAN",
            actor_id="user-a",
        )
    monkeypatch.undo()

    fresh_post = content_repo.get_post(topic_id=topic.topic_id, post_id=post.post_id)
    assert fresh_post is not None and fresh_post.helpful is False
    with db.orm_session() as session:
        assert session.query(ForumInteractionModel).count() == 0


def test_helpful_accept_is_emitted_once_and_cleared_without_negative_event(db):
    content_repo = ForumRepository(db)
    topic = content_repo.create_topic(
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-help-topic",
        title="Help",
        body="Help",
        topic_type="HELP",
    ).topic
    post = content_repo.create_reply(
        topic_id=topic.topic_id,
        author_type="HUMAN",
        author_id="user-b",
        client_request_id="req-help-post",
        body="Answer",
    ).post

    ForumService(content_repo).set_post_helpful(
        topic_id=topic.topic_id,
        post_id=post.post_id,
        author_type="HUMAN",
        author_id="user-a",
        helpful=True,
    )
    ForumService(content_repo).set_post_helpful(
        topic_id=topic.topic_id,
        post_id=post.post_id,
        author_type="HUMAN",
        author_id="user-a",
        helpful=True,
    )
    ForumService(content_repo).set_post_helpful(
        topic_id=topic.topic_id,
        post_id=post.post_id,
        author_type="HUMAN",
        author_id="user-a",
        helpful=False,
    )

    with db.orm_session() as session:
        rows = session.query(
            ForumInteractionModel.action_type,
            ForumInteractionModel.target_type,
            ForumInteractionModel.target_id,
        ).all()
    assert len(rows) == 1
    assert rows[0] == (
        "ACCEPT",
        "POST",
        post.post_id,
    )
