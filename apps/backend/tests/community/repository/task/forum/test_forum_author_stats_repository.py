"""Repository tests for author production aggregates in the BBS domain."""

from __future__ import annotations

from agentclaw.community.core.repository.implementations.task.forum.forum_author_stats_repository import (
    ForumAuthorStatsRepository,
)
from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    ForumRepository,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
)
from agentclaw.community.core.task.forum.services.forum_service import ForumService
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def test_author_stats_count_current_topic_post_and_helpful_replies(db):
    repo = ForumRepository(db)
    stats_repo = ForumAuthorStatsRepository(db)
    topic = repo.create_topic(
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-author-topic",
        title="Help topic",
        body="Please help",
        topic_type="HELP",
    ).topic
    other_topic = repo.create_topic(
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-author-other",
        title="Other",
        body="Other",
    ).topic
    helpful = repo.create_reply(
        topic_id=topic.topic_id,
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-helpful",
        body="Helpful reply",
    ).post
    repo.create_reply(
        topic_id=other_topic.topic_id,
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-normal",
        body="Normal reply",
    ).post
    repo.create_reply(
        topic_id=topic.topic_id,
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-child",
        body="Child reply",
        reply_to_post_id=helpful.post_id,
    ).post

    ForumService(repo).set_post_helpful(
        topic_id=topic.topic_id,
        post_id=helpful.post_id,
        author_type="HUMAN",
        author_id="user-a",
        helpful=True,
    )

    actor = ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
    stats = stats_repo.get_author_stats(actor)

    assert stats.topic_count == 2
    assert stats.reply_count == 3
    assert stats.accepted_reply_count == 1


def test_author_stats_are_tenant_and_environment_scoped(db, monkeypatch):
    with avernet_tenant_scope("tenant-a"):
        ForumRepository(db).create_topic(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-tenant-a",
            title="A",
            body="A",
        )
    with avernet_tenant_scope("tenant-b"):
        ForumRepository(db).create_topic(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-tenant-b",
            title="B",
            body="B",
        )

    with avernet_tenant_scope("tenant-b"):
        stats = ForumAuthorStatsRepository(db).get_author_stats(
            ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
        )

    assert stats.topic_count == 1

    monkeypatch.setenv("SERVER_ENV", "pre")
    with avernet_tenant_scope("tenant-a"):
        pre_stats = ForumAuthorStatsRepository(db).get_author_stats(
            ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
        )
    assert pre_stats.topic_count == 0
