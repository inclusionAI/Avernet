"""Repository tests for the unified personal-center activity projection."""

from __future__ import annotations

from datetime import datetime, timedelta

from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    ForumRepository,
)
from agentclaw.community.core.repository.implementations.task.forum.personal_center_activity_repository import (
    ForumPersonalCenterActivityRepository,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    ForumInteractionTargetType,
    PersonalCenterActivityType,
    PersonalCenterActorScope,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumInteractionModel,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

HUMAN = ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
BOT = ForumActorRef(type=ForumActorType.BOT, id="bot-a")
ALL_ACTORS = (HUMAN, BOT)


def _list(
    repo,
    *,
    actors=ALL_ACTORS,
    activity_type=PersonalCenterActivityType.ALL,
    target_type=None,
    page=1,
    page_size=20,
):
    return repo.list_activities(
        actors=actors,
        actor_scope=PersonalCenterActorScope.ALL,
        activity_type=activity_type,
        target_type=target_type,
        page=page,
        page_size=page_size,
    )


def test_activity_projection_mixes_actors_targets_and_activity_sources(db):
    content_repo = ForumRepository(db)
    activity_repo = ForumPersonalCenterActivityRepository(db)
    topic = content_repo.create_topic(
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-topic",
        title="Human topic",
        body="Human body",
    ).topic
    post = content_repo.create_reply(
        topic_id=topic.topic_id,
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="req-post",
        body="Human direct reply",
    ).post
    child = content_repo.create_reply(
        topic_id=topic.topic_id,
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-child",
        body="Bot child reply",
        reply_to_post_id=post.post_id,
    ).post
    bot_topic = content_repo.create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-bot-topic",
        title="Bot topic",
        body="Bot body",
    ).topic

    with db.orm_session() as session:
        base = datetime(2026, 10, 27, 12, 0)
        rows = [
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="VIEW",
                target_type="TOPIC",
                target_id=topic.topic_id,
                env="dev",
                gmt_create=base,
                gmt_modified=base,
            ),
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="VIEW",
                target_type="TOPIC",
                target_id=bot_topic.topic_id,
                env="dev",
                active=0,
                gmt_create=base + timedelta(minutes=1),
                gmt_modified=base + timedelta(minutes=1),
            ),
            ForumInteractionModel(
                actor_type="BOT",
                actor_id="bot-a",
                action_type="FAVORITE",
                target_type="TOPIC",
                target_id=bot_topic.topic_id,
                env="dev",
                gmt_create=base + timedelta(minutes=2),
                gmt_modified=base + timedelta(minutes=2),
            ),
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="LIKE",
                target_type="TOPIC",
                target_id=topic.topic_id,
                env="dev",
                gmt_create=base + timedelta(minutes=3),
                gmt_modified=base + timedelta(minutes=3),
            ),
            ForumInteractionModel(
                actor_type="BOT",
                actor_id="bot-a",
                action_type="LIKE",
                target_type="POST",
                target_id=child.post_id,
                env="dev",
                gmt_create=base + timedelta(minutes=4),
                gmt_modified=base + timedelta(minutes=4),
            ),
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="FOLLOW",
                target_type="HUMAN",
                target_id="user-b",
                env="dev",
                gmt_create=base + timedelta(minutes=5),
                gmt_modified=base + timedelta(minutes=5),
            ),
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="FOLLOW",
                target_type="BOT",
                target_id="bot-b",
                env="dev",
                gmt_create=base + timedelta(minutes=6),
                gmt_modified=base + timedelta(minutes=6),
            ),
        ]
        session.add_all(rows)

    page = _list(activity_repo)

    keys = {
        (
            item.activity_type,
            item.actor_type,
            item.target_type,
            item.target_id,
            item.post_id,
        )
        for item in page.items
    }
    assert page.total >= 9
    assert (PersonalCenterActivityType.AUTHORED, "HUMAN", "TOPIC", topic.topic_id, None) in keys
    assert (PersonalCenterActivityType.AUTHORED, "BOT", "TOPIC", bot_topic.topic_id, None) in keys
    assert (PersonalCenterActivityType.VIEWED, "HUMAN", "TOPIC", topic.topic_id, None) in keys
    assert (PersonalCenterActivityType.FAVORITED, "BOT", "TOPIC", bot_topic.topic_id, None) in keys
    assert (PersonalCenterActivityType.LIKED, "HUMAN", "TOPIC", topic.topic_id, None) in keys
    assert (PersonalCenterActivityType.LIKED, "BOT", "POST", child.post_id, child.post_id) in keys
    assert (PersonalCenterActivityType.FOLLOWED, "HUMAN", "HUMAN", "user-b", None) in keys
    assert (PersonalCenterActivityType.FOLLOWED, "HUMAN", "BOT", "bot-b", None) in keys
    assert (PersonalCenterActivityType.REPLIED, "HUMAN", "TOPIC", topic.topic_id, post.post_id) in keys
    assert (PersonalCenterActivityType.REPLIED, "BOT", "POST", post.post_id, child.post_id) in keys

    viewed = _list(activity_repo, activity_type=PersonalCenterActivityType.VIEWED)
    assert viewed.total == 1
    assert viewed.items[0].happened_at == base
    topic_context = viewed.items[0].topic_context
    assert topic_context is not None
    assert topic_context.title == "Human topic"
    assert topic_context.body_preview == "Human body"
    assert topic_context.topic_type == "DISCUSSION"
    assert topic_context.author_type == ForumActorType.HUMAN
    assert topic_context.author_id == "user-a"
    assert topic_context.reply_count == 2
    assert topic_context.like_count == 1
    assert topic_context.latest_reply_at is not None

    nested_reply = next(
        item
        for item in page.items
        if item.activity_type is PersonalCenterActivityType.REPLIED
        and item.actor_type == ForumActorType.BOT
    )
    assert nested_reply.replied_to_post is not None
    assert nested_reply.replied_to_post.post_id == post.post_id
    assert nested_reply.replied_to_post.author_type == ForumActorType.HUMAN
    assert nested_reply.replied_to_post.author_id == "user-a"
    assert nested_reply.replied_to_post.author_display_name == "user-a"
    assert nested_reply.replied_to_post.body_preview == "Human direct reply"

    liked_topic = _list(
        activity_repo,
        activity_type=PersonalCenterActivityType.LIKED,
        target_type=ForumInteractionTargetType.TOPIC,
    )
    assert liked_topic.total == 1
    assert liked_topic.items[0].target_id == topic.topic_id
    assert liked_topic.items[0].post_id is None

    all_human = _list(
        activity_repo,
        activity_type=PersonalCenterActivityType.ALL,
        target_type=ForumInteractionTargetType.HUMAN,
    )
    assert all_human.total == 1
    assert [item.activity_type for item in all_human.items] == [
        PersonalCenterActivityType.FOLLOWED
    ]
    assert all_human.items[0].target_id == "user-b"


def test_followed_identity_activity_keeps_only_latest_active_state(db):
    content_repo = ForumRepository(db)
    activity_repo = ForumPersonalCenterActivityRepository(db)
    content_repo.create_topic(
        author_type="BOT",
        author_id="bot-b",
        client_request_id="req-followed-bot-topic",
        title="Bot topic",
        body="Bot body",
        author_display_name="协作 Bot",
    )
    with db.orm_session() as session:
        base = datetime(2026, 10, 27, 12, 0)
        session.add_all(
            [
                ForumInteractionModel(
                    actor_type="HUMAN",
                    actor_id="user-a",
                    action_type="FOLLOW",
                    target_type="HUMAN",
                    target_id="user-b",
                    env="dev",
                    active=0,
                    gmt_create=base,
                    gmt_modified=base,
                ),
                ForumInteractionModel(
                    actor_type="HUMAN",
                    actor_id="user-a",
                    action_type="FOLLOW",
                    target_type="BOT",
                    target_id="bot-b",
                    env="dev",
                    gmt_create=base + timedelta(minutes=2),
                    gmt_modified=base + timedelta(minutes=2),
                ),
            ]
        )

    followed = _list(
        activity_repo,
        actors=(HUMAN,),
        activity_type=PersonalCenterActivityType.FOLLOWED,
    )

    assert followed.total == 1
    assert followed.items[0].activity_type is PersonalCenterActivityType.FOLLOWED
    assert followed.items[0].target_type == ForumInteractionTargetType.BOT
    assert followed.items[0].target_id == "bot-b"
    assert followed.items[0].topic_id is None
    assert followed.items[0].post_id is None
    assert followed.items[0].body_preview == "协作 Bot"

    human_only = _list(
        activity_repo,
        actors=(HUMAN,),
        activity_type=PersonalCenterActivityType.FOLLOWED,
        target_type=ForumInteractionTargetType.HUMAN,
    )

    assert human_only.total == 0

    topic_filter = _list(
        activity_repo,
        actors=(HUMAN,),
        activity_type=PersonalCenterActivityType.FOLLOWED,
        target_type=ForumInteractionTargetType.TOPIC,
    )

    assert topic_filter.total == 0


def test_activity_pages_orders_states_on_the_database(db):
    content_repo = ForumRepository(db)
    activity_repo = ForumPersonalCenterActivityRepository(db)
    topic_ids = []
    for index in range(3):
        topic = content_repo.create_topic(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id=f"req-topic-{index}",
            title=f"Topic {index}",
            body="Body",
        ).topic
        topic_ids.append(topic.topic_id)
        with db.orm_session() as session:
            session.add(
                ForumInteractionModel(
                    actor_type="HUMAN",
                    actor_id="user-a",
                    action_type="VIEW",
                    target_type="TOPIC",
                    target_id=topic.topic_id,
                    env="dev",
                    gmt_create=datetime(2026, 10, 27, 12, index * 10),
                    gmt_modified=datetime(2026, 10, 27, 12, index * 10),
                )
            )

    page = _list(activity_repo, activity_type=PersonalCenterActivityType.VIEWED)

    assert page.total == 3
    assert [item.target_id for item in page.items] == list(reversed(topic_ids))

    first = _list(
        activity_repo, activity_type=PersonalCenterActivityType.VIEWED, page_size=2
    )
    second = _list(
        activity_repo, activity_type=PersonalCenterActivityType.VIEWED, page=2, page_size=2
    )

    assert first.total == second.total == 3
    assert [item.target_id for item in first.items] == list(reversed(topic_ids))[:2]
    assert [item.target_id for item in second.items] == [topic_ids[0]]


def test_liked_post_activity_is_fenced_by_current_topic_tenant_and_environment(db, monkeypatch):
    content_repo = ForumRepository(db)
    activity_repo = ForumPersonalCenterActivityRepository(db)

    monkeypatch.setenv("SERVER_ENV", "pre")
    with avernet_tenant_scope("tenant-a"):
        stale_topic = content_repo.create_topic(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-stale",
            title="Stale",
            body="Stale",
        ).topic
        stale_post = content_repo.create_reply(
            topic_id=stale_topic.topic_id,
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-stale-post",
            body="Stale reply",
        ).post

    monkeypatch.setenv("SERVER_ENV", "")
    with avernet_tenant_scope("tenant-b"):
        current_topic = content_repo.create_topic(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-current",
            title="Current",
            body="Current",
        ).topic
        current_post = content_repo.create_reply(
            topic_id=current_topic.topic_id,
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-current-post",
            body="Current reply",
        ).post

    with db.orm_session() as session:
        for target_id in (stale_post.post_id, current_post.post_id):
            session.add(
                ForumInteractionModel(
                    actor_type="HUMAN",
                    actor_id="user-a",
                    action_type="LIKE",
                    target_type="POST",
                    target_id=target_id,
                    env="dev",
                    gmt_create=datetime(2026, 10, 27, 12, 0),
                    gmt_modified=datetime(2026, 10, 27, 12, 0),
                )
            )

    with avernet_tenant_scope("tenant-b"):
        page = _list(
            activity_repo,
            actors=(HUMAN,),
            activity_type=PersonalCenterActivityType.LIKED,
            target_type=ForumInteractionTargetType.POST,
        )

    assert page.total == 1
    assert page.items[0].target_id == current_post.post_id
    assert page.items[0].topic_id == current_topic.topic_id
