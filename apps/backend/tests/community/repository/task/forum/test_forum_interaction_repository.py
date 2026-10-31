"""Repository tests for BBS interaction states and summaries."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from agentclaw.community.core.errors import ValidationError

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
    ForumSummaryScope,
    ForumTargetRef,
)
from agentclaw.community.core.task.forum.interaction.time_utils import storage_now
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


def _events(repo, *, actor_type="HUMAN", actor_id="user-a", active=1):
    def add(action, target_type, target_id):
        repo.insert_interaction(
            actor_type=actor_type,
            actor_id=actor_id,
            action_type=action,
            target_type=target_type,
            target_id=target_id,
            active=active,
        )

    return add


def test_interaction_repository_upserts_repeated_actor_target_state(db):
    repo = ForumInteractionRepository(db)

    before = storage_now()
    first = repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
        active=1,
    )
    between = storage_now()
    second = repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
        active=-1,
    )
    after = storage_now()

    assert first.active == 1
    assert second.active == 0
    assert before <= first.created_at <= between
    assert first.created_at == first.updated_at
    assert second.created_at == first.created_at
    assert between <= second.updated_at <= after
    assert second.updated_at >= first.updated_at
    with db.orm_session() as session:
        rows = session.query(
            ForumInteractionModel.action_type,
            ForumInteractionModel.target_id,
            ForumInteractionModel.active,
        ).all()
    assert len(rows) == 1
    assert rows[0] == ("LIKE", "topic-1", 0)


def test_interaction_summaries_exclude_inactive_state(db):
    repo = ForumInteractionRepository(db)
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id="topic-1")

    repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
        active=1,
    )
    repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
        active=-1,
    )
    assert repo.batch_get_target_summaries((target,))[target].like_count == 0

    repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="LIKE",
        target_type="TOPIC",
        target_id="topic-1",
        active=1,
    )
    assert repo.batch_get_target_summaries((target,))[target].like_count == 1


def test_three_distinct_likers_accumulate_topic_like_count(db):
    repo = ForumInteractionRepository(db)
    topic_id = "topic-three-likers"
    likers = ("user-a", "user-b", "user-c")
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id=topic_id)

    for actor_id in likers:
        repo.insert_interaction(
            actor_type="HUMAN",
            actor_id=actor_id,
            action_type="LIKE",
            target_type="TOPIC",
            target_id=topic_id,
            active=1,
        )

    target_stats = repo.batch_get_target_summaries((target,))[target]
    pairs = tuple(
        ForumActorTargetRef(
            actor=ForumActorRef(type=ForumActorType.HUMAN, id=actor_id),
            target=target,
        )
        for actor_id in likers
    )
    pair_stats = repo.batch_get_actor_target_summaries(pairs)

    assert target_stats.like_count == 3
    assert all(pair_stats[pair].like_count == 1 for pair in pairs)
    with db.orm_session() as session:
        rows = session.query(
            ForumInteractionModel.actor_id,
            ForumInteractionModel.active,
        ).filter(
            ForumInteractionModel.action_type == "LIKE",
            ForumInteractionModel.target_type == "TOPIC",
            ForumInteractionModel.target_id == topic_id,
        ).order_by(ForumInteractionModel.actor_id).all()
    assert rows == [("user-a", 1), ("user-b", 1), ("user-c", 1)]


def test_three_distinct_viewers_accumulate_topic_view_count(db):
    repo = ForumInteractionRepository(db)
    topic_id = "topic-three-viewers"
    viewers = ("user-a", "user-b", "user-c")
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id=topic_id)

    for actor_id in viewers:
        repo.insert_interaction(
            actor_type="HUMAN",
            actor_id=actor_id,
            action_type="VIEW",
            target_type="TOPIC",
            target_id=topic_id,
            active=1,
        )

    target_stats = repo.batch_get_target_summaries((target,))[target]
    pairs = tuple(
        ForumActorTargetRef(
            actor=ForumActorRef(type=ForumActorType.HUMAN, id=actor_id),
            target=target,
        )
        for actor_id in viewers
    )
    pair_stats = repo.batch_get_actor_target_summaries(pairs)

    assert target_stats.view_count == 3
    assert all(pair_stats[pair].view_count == 1 for pair in pairs)
    with db.orm_session() as session:
        rows = session.query(
            ForumInteractionModel.actor_id,
            ForumInteractionModel.active,
        ).filter(
            ForumInteractionModel.action_type == "VIEW",
            ForumInteractionModel.target_type == "TOPIC",
            ForumInteractionModel.target_id == topic_id,
        ).order_by(ForumInteractionModel.actor_id).all()
    assert rows == [("user-a", 1), ("user-b", 1), ("user-c", 1)]


def test_first_cancel_delta_without_existing_state_is_rejected(db):
    repo = ForumInteractionRepository(db)

    for invalid_active in (0, True, False, "1"):
        with pytest.raises(ValidationError, match="active must be 1 or -1"):
            repo.insert_interaction(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="LIKE",
                target_type="TOPIC",
                target_id="topic-1",
                active=invalid_active,
            )
    with pytest.raises(ValidationError, match="first interaction must"):
        repo.insert_interaction(
            actor_type="HUMAN",
            actor_id="user-a",
            action_type="LIKE",
            target_type="TOPIC",
            target_id="topic-1",
            active=-1,
        )
    with db.orm_session() as session:
        assert session.query(ForumInteractionModel).count() == 0


def test_repeated_view_deltas_accumulate_into_view_count(db):
    repo = ForumInteractionRepository(db)
    target = ForumTargetRef(type=ForumInteractionTargetType.TOPIC, id="topic-1")
    actor = ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
    pair = ForumActorTargetRef(actor=actor, target=target)

    first = repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="VIEW",
        target_type="TOPIC",
        target_id="topic-1",
        active=1,
    )
    second = repo.insert_interaction(
        actor_type="HUMAN",
        actor_id="user-a",
        action_type="VIEW",
        target_type="TOPIC",
        target_id="topic-1",
        active=1,
    )

    assert first.active == 1
    assert second.active == 2
    assert repo.batch_get_target_summaries((target,))[target].view_count == 2
    assert repo.batch_get_actor_target_summaries((pair,))[pair].view_count == 2


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

    assert (target.view_count, target.like_count, target.follow_count) == (1, 1, 1)
    assert target.reply_participant_count == 2
    assert target.helpful_reply_count == 0
    assert actor.follow_count == 1
    assert (actor.view_count, actor.like_count, actor.favorite_count) == (0, 0, 0)
    assert (pair.view_count, pair.like_count, pair.follow_count) == (1, 1, 0)
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


def test_interaction_ddl_and_orm_contract_define_active_unique_state():
    ddl_path = Path(
        "src/agentclaw/community/core/task/forum/sql/2026_10_27_forum_interaction.sql"
    )
    migration_path = Path(
        "src/agentclaw/community/core/task/forum/sql/"
        "2026_10_28_forum_interaction_active_unique.sql"
    )
    amount_migration_path = Path(
        "src/agentclaw/community/core/task/forum/sql/"
        "2026_10_28_forum_interaction_active_unique_amount.sql"
    )
    ddl = ddl_path.read_text()
    migration = migration_path.read_text()

    assert "ac_forum_interaction" in ddl
    assert "`active`          int          NOT NULL DEFAULT 1" in ddl
    assert "UNIQUE KEY `uk_forum_interaction_action`" in ddl
    assert (
        "(`env`, `actor_type`, `actor_id`, `action_type`, `target_type`, `target_id`)"
        in ddl
    )
    assert "`active` int NOT NULL DEFAULT 1" in migration
    assert "ADD UNIQUE KEY `uk_forum_interaction_action`" in migration
    assert (
        "MODIFY COLUMN `active` int NOT NULL DEFAULT 1" in amount_migration_path.read_text()
    )
    columns = {column.name for column in ForumInteractionModel.__table__.columns}
    assert columns == {
        "id",
        "actor_type",
        "actor_id",
        "action_type",
        "target_type",
        "target_id",
        "env",
        "active",
        "gmt_create",
        "gmt_modified",
    }
    indexes = {
    index.name: index.unique for index in ForumInteractionModel.__table__.indexes
}
    assert indexes == {
        "uk_forum_interaction_action": True,
        "idx_forum_interaction_target": False,
        "idx_forum_interaction_actor": False,
    }


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


def test_helpful_accept_updates_one_state_row_to_active(db):
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
            ForumInteractionModel.active,
        ).all()
    assert len(rows) == 1
    assert rows[0] == (
        "ACCEPT",
        "POST",
        post.post_id,
        0,
    )
