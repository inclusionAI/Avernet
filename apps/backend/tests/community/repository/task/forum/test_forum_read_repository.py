from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
)
from agentclaw.community.core.task.forum.models import (
    TOPIC_STATUS_CLOSED,
    TOPIC_TYPE_HELP,
    TOPIC_TYPE_NOTICE,
    TOPIC_TYPE_POLL,
    ForumPollConfig,
    ForumPollResultOptionRecord,
    ForumPollSelection,
    ForumTopicViewerRecord,
    ForumTopicSummaryRecord,
    ForumTopicConfig,
)
from agentclaw.community.core.repository.implementations.task.forum import (
    ForumRepository,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumInteractionModel,
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def _topic(repo, request_id, *, title, body="description", topic_type="DISCUSSION"):
    return repo.create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id=request_id,
        title=title,
        body=body,
        topic_type=topic_type,
    ).topic


def _reply(
    repo,
    topic_id,
    request_id,
    body,
    author_type="HUMAN",
    author_id="bot-b",
    reply_to_post_id=None,
):
    return repo.create_reply(
        topic_id=topic_id,
        author_type=author_type,
        author_id=author_id,
        client_request_id=request_id,
        body=body,
        reply_to_post_id=reply_to_post_id,
    ).post


def _topic_interaction(
    session, topic_id, actor_id, action_type, *, created_at=None
):
    interaction = ForumInteractionModel(
        actor_type="HUMAN",
        actor_id=actor_id,
        action_type=action_type,
        target_type="TOPIC",
        target_id=topic_id,
        env=get_current_env(),
        gmt_create=created_at or datetime.now(timezone.utc),
        gmt_modified=created_at or datetime.now(timezone.utc),
    )
    session.add(interaction)


def test_list_topics_filters_status_searches_literal_wildcards_and_pages(db):
    repo = ForumRepository(db)
    first = _topic(repo, "req-1", title="100% ready", body="plain")
    second = _topic(repo, "req-2", title="other", body="uses_name")
    _topic(repo, "req-3", title="ordinary", body="plain")
    with db.orm_session() as session:
        session.query(ForumTopicModel).filter_by(
            topic_id=second.topic_id
        ).one().status = TOPIC_STATUS_CLOSED

    percent = repo.list_topics(keyword="%", status=None, offset=0, limit=20)
    underscore = repo.list_topics(keyword="_", status=None, offset=0, limit=20)
    closed = repo.list_topics(
        keyword=None, status=TOPIC_STATUS_CLOSED, offset=0, limit=20
    )
    beyond = repo.list_topics(keyword=None, status=None, offset=10, limit=2)

    assert percent.total == 1
    assert [item.topic_id for item in percent.items] == [first.topic_id]
    assert underscore.total == 1
    assert [item.topic_id for item in underscore.items] == [second.topic_id]
    assert closed.total == 1
    assert [item.topic_id for item in closed.items] == [second.topic_id]
    assert beyond.total == 3
    assert beyond.items == ()


def test_list_topics_filters_active_followers_for_owner(db):
    repo = ForumRepository(db)
    followed_bot_topic = repo.create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-follow-bot",
        title="followed bot",
        body="description",
    ).topic
    followed_human_topic = repo.create_topic(
        author_type="HUMAN",
        author_id="user-b",
        client_request_id="req-follow-human",
        title="followed human",
        body="description",
    ).topic
    repo.create_topic(
        author_type="BOT",
        author_id="bot-b",
        client_request_id="req-unfollowed",
        title="unfollowed bot",
        body="description",
    )

    with db.orm_session() as session:
        session.add(
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="FOLLOW",
                target_type="BOT",
                target_id="bot-a",
                env=get_current_env(),
                active=1,
            )
        )
        session.add(
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="FOLLOW",
                target_type="HUMAN",
                target_id="user-b",
                env=get_current_env(),
                active=1,
            )
        )
        session.add(
            ForumInteractionModel(
                actor_type="HUMAN",
                actor_id="user-a",
                action_type="FOLLOW",
                target_type="BOT",
                target_id="bot-b",
                env=get_current_env(),
                active=0,
            )
        )

    followed_authors = repo.list_followed_authors(owner_user_id="user-a")
    assert followed_authors == (
        ForumActorRef(type=ForumActorType.BOT, id="bot-a"),
        ForumActorRef(type=ForumActorType.HUMAN, id="user-b"),
    )

    result = repo.list_topics(
        keyword=None,
        status=None,
        offset=0,
        limit=20,
        followed_authors=followed_authors,
    )

    assert result.total == 2
    assert {item.topic_id for item in result.items} == {
        followed_bot_topic.topic_id,
        followed_human_topic.topic_id,
    }


def test_list_topics_topic_type_filter_is_optional_and_combines_with_status(db):
    repo = ForumRepository(db)
    discussion = _topic(repo, "discussion", title="discussion")
    poll = _topic(repo, "poll", title="poll", topic_type=TOPIC_TYPE_POLL)
    notice = _topic(repo, "notice", title="notice", topic_type=TOPIC_TYPE_NOTICE)
    with db.orm_session() as session:
        session.query(ForumTopicModel).filter_by(
            topic_id=notice.topic_id
        ).one().status = TOPIC_STATUS_CLOSED

    all_types = repo.list_topics(
        keyword=None, status=None, topic_type=None, offset=0, limit=20
    )
    polls = repo.list_topics(
        keyword=None, status=None, topic_type=TOPIC_TYPE_POLL, offset=0, limit=20
    )
    closed_notices = repo.list_topics(
        keyword=None,
        status=TOPIC_STATUS_CLOSED,
        topic_type=TOPIC_TYPE_NOTICE,
        offset=0,
        limit=20,
    )

    assert {item.topic_id for item in all_types.items} == {
        discussion.topic_id,
        poll.topic_id,
        notice.topic_id,
    }
    assert [item.topic_id for item in polls.items] == [poll.topic_id]
    assert [item.topic_id for item in closed_notices.items] == [notice.topic_id]


def test_list_topics_uses_id_as_stable_tie_breaker(db):
    repo = ForumRepository(db)
    first = _topic(repo, "req-1", title="first")
    second = _topic(repo, "req-2", title="second")
    same_time = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    with db.orm_session() as session:
        session.query(ForumTopicModel).update({ForumTopicModel.gmt_create: same_time})

    result = repo.list_topics(keyword=None, status=None, offset=0, limit=20)

    assert [item.topic_id for item in result.items] == [second.topic_id, first.topic_id]


def test_list_topics_puts_hot_topics_before_time_sorted_ordinary_topics(db):
    repo = ForumRepository(db)
    old_ordinary = _topic(repo, "hot-old-ordinary", title="ordinary old")
    hot = _topic(repo, "hot-topic", title="hot topic")
    recent_ordinary = _topic(repo, "hot-recent-ordinary", title="ordinary recent")
    _reply(repo, hot.topic_id, "hot-reply-1", "one", author_id="user-1")
    _reply(repo, hot.topic_id, "hot-reply-2", "two", author_id="user-2")

    with db.orm_session() as session:
        for actor_id in ("user-1", "user-2"):
            _topic_interaction(session, hot.topic_id, actor_id, "FAVORITE")
            _topic_interaction(session, hot.topic_id, actor_id, "LIKE")

    result = repo.list_topics(keyword=None, status=None, offset=0, limit=20)

    assert [item.topic_id for item in result.items] == [
        hot.topic_id,
        recent_ordinary.topic_id,
        old_ordinary.topic_id,
    ]
    assert result.items[0].is_hot is True
    assert result.items[1].is_hot is False
    assert result.items[2].is_hot is False


def test_hot_list_gate_ignores_follow_and_counts_unique_actors(db):
    repo = ForumRepository(db)
    topic = _topic(repo, "hot-low-interaction", title="low interaction")
    _reply(repo, topic.topic_id, "low-reply", "same user", author_id="user-1")

    with db.orm_session() as session:
        _topic_interaction(session, topic.topic_id, "user-1", "FAVORITE")
        _topic_interaction(session, topic.topic_id, "user-2", "LIKE")
        for actor_id in ("follow-1", "follow-2", "follow-3"):
            _topic_interaction(session, topic.topic_id, actor_id, "FOLLOW")

    result = repo.list_topics(keyword=None, status=None, offset=0, limit=20)

    assert result.items[0].is_hot is False


def test_hot_list_gate_excludes_topics_older_than_three_days(db):
    repo = ForumRepository(db)
    topic = _topic(repo, "hot-expired", title="expired hot")
    created_at = datetime(2026, 10, 20, 0, 0)

    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(
            topic_id=topic.topic_id
        ).one()
        row.gmt_create = created_at
        row.gmt_modified = created_at
        for actor_id in ("user-1", "user-2"):
            _topic_interaction(
                session,
                topic.topic_id,
                actor_id,
                "FAVORITE",
                created_at=created_at,
            )
            _topic_interaction(
                session,
                topic.topic_id,
                actor_id,
                "LIKE",
                created_at=created_at,
            )

    result = repo.list_topics(keyword=None, status=None, offset=0, limit=20)

    assert result.items[0].is_hot is False


def test_topic_reads_are_isolated_by_tenant_and_environment(db, monkeypatch):
    repo = ForumRepository(db)
    with avernet_tenant_scope("tenant-a"):
        tenant_topic = _topic(repo, "tenant-a-topic", title="tenant A")
    with avernet_tenant_scope("tenant-b"):
        _topic(repo, "tenant-b-topic", title="tenant B")

    with avernet_tenant_scope("tenant-a"):
        assert repo.get_topic(tenant_topic.topic_id) is not None
        assert (
            repo.list_topics(keyword=None, status=None, offset=0, limit=20).total == 1
        )
    with avernet_tenant_scope("tenant-b"):
        assert repo.get_topic(tenant_topic.topic_id) is None
        assert (
            repo.list_topics(keyword=None, status=None, offset=0, limit=20).total == 1
        )

    monkeypatch.setenv("SERVER_ENV", "pre")
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_topic(tenant_topic.topic_id) is None
        assert (
            repo.list_topics(keyword=None, status=None, offset=0, limit=20).total == 0
        )


def test_get_topic_returns_persisted_tags_and_poll_result(db):
    repo = ForumRepository(db)
    topic = repo.create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-poll",
        title="poll",
        body="Choose one",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(selection="SINGLE", options=("Option A", "Option B"))
        ),
    ).topic

    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).one()
        row.analysis_result = {
            "summary": {"content": "The topic asks participants to choose one option."},
            "tags": ["AI治理", "投票"],
            "poll_stats": {
                "total_votes": 3,
                "participant_count": 3,
                "by_option": [
                    {"index": 0, "votes": 2, "percent": 67},
                    {"index": 1, "votes": 1, "percent": 33},
                ],
            },
        }

    result = repo.get_topic(topic.topic_id)

    assert result is not None
    assert result.analysis.tags == ("AI治理", "投票")
    assert result.analysis.summary == ForumTopicSummaryRecord(
        content="The topic asks participants to choose one option."
    )
    assert result.analysis.poll_result is not None
    assert result.analysis.poll_result.total_votes == 3
    assert result.analysis.poll_result.participant_count == 3
    assert result.analysis.poll_result.options == (
        ForumPollResultOptionRecord(index=0, votes=2, percent=67),
        ForumPollResultOptionRecord(index=1, votes=1, percent=33),
    )


def test_get_topic_returns_zero_vote_poll_result_before_task_writes_projection(db):
    repo = ForumRepository(db)
    topic = repo.create_topic(
        author_type="BOT",
        author_id="bot-a",
        client_request_id="req-poll-empty",
        title="poll",
        body="Choose one",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(selection="MULTI", options=("A", "B"))
        ),
    ).topic

    result = repo.get_topic(topic.topic_id)

    assert result is not None
    assert result.analysis is not None
    assert result.analysis.summary is None
    assert result.analysis.tags == ()
    assert result.analysis.poll_result is not None
    assert result.analysis.poll_result.total_votes == 0
    assert result.analysis.poll_result.participant_count == 0
    assert result.analysis.poll_result.options == (
        ForumPollResultOptionRecord(index=0, votes=0, percent=0),
        ForumPollResultOptionRecord(index=1, votes=0, percent=0),
    )


def test_list_posts_orders_chronologically_and_keeps_total_on_empty_page(db):
    repo = ForumRepository(db)
    topic = _topic(repo, "topic", title="topic")
    first = _reply(repo, topic.topic_id, "post-1", "first")
    second = _reply(repo, topic.topic_id, "post-2", "second")
    same_time = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    with db.orm_session() as session:
        session.query(ForumPostModel).update({ForumPostModel.gmt_create: same_time})

    result = repo.list_posts(topic_id=topic.topic_id, offset=0, limit=20)
    beyond = repo.list_posts(topic_id=topic.topic_id, offset=10, limit=20)

    assert result.total == 2
    assert [item.post_id for item in result.items] == [first.post_id, second.post_id]
    assert beyond.total == 2
    assert beyond.items == ()


def test_list_posts_returns_reference_and_two_level_floor_groups(db):
    repo = ForumRepository(db)
    topic = _topic(repo, "topic", title="topic")
    first_floor = _reply(repo, topic.topic_id, "post-1", "first floor")
    second_floor = _reply(repo, topic.topic_id, "post-2", "second floor")
    child = _reply(
        repo,
        topic.topic_id,
        "post-3",
        "reply to first floor",
        reply_to_post_id=first_floor.post_id,
    )
    grandchild = _reply(
        repo,
        topic.topic_id,
        "post-4",
        "reply to the child",
        reply_to_post_id=child.post_id,
    )

    first_page = repo.list_posts(topic_id=topic.topic_id, offset=0, limit=1)
    second_page = repo.list_posts(topic_id=topic.topic_id, offset=1, limit=1)
    beyond = repo.list_posts(topic_id=topic.topic_id, offset=2, limit=1)

    assert first_page.total == 2
    assert [item.post_id for item in first_page.items] == [first_floor.post_id]
    assert first_page.items[0].floor_no == 1
    assert [
        item.post_id for item in first_page.items[0].children
    ] == [child.post_id, grandchild.post_id]
    assert first_page.items[0].children[0].reply_to_post_id == first_floor.post_id
    assert first_page.items[0].children[0].reply_to_floor_no == 1
    # A reply to a child has no floor-bearing target; it stays in the same group.
    assert first_page.items[0].children[1].reply_to_post_id == child.post_id
    assert first_page.items[0].children[1].reply_to_floor_no is None

    assert second_page.items[0].post_id == second_floor.post_id
    assert second_page.items[0].floor_no == 2
    assert second_page.items[0].children == ()
    assert beyond.total == 2
    assert beyond.items == ()


def test_list_posts_prioritizes_helpful_before_paging_without_renumbering_floors(db):
    repo = ForumRepository(db)
    topic = _topic(repo, "help-topic", title="help", topic_type=TOPIC_TYPE_HELP)
    first = _reply(repo, topic.topic_id, "help-post-1", "ordinary first")
    second = _reply(repo, topic.topic_id, "help-post-2", "helpful second")
    child = _reply(
        repo,
        topic.topic_id,
        "help-post-child",
        "child of helpful second",
        reply_to_post_id=second.post_id,
    )
    third = _reply(repo, topic.topic_id, "help-post-3", "helpful third")
    fourth = _reply(repo, topic.topic_id, "help-post-4", "ordinary fourth")
    base = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    times = {
        first.post_id: base,
        second.post_id: base.replace(minute=1),
        child.post_id: base.replace(minute=2),
        third.post_id: base.replace(minute=3),
        fourth.post_id: base.replace(minute=4),
    }
    with db.orm_session() as session:
        rows = session.query(ForumPostModel).filter_by(topic_id=topic.topic_id).all()
        for row in rows:
            row.gmt_create = times[row.post_id]
        session.query(ForumPostModel).filter(
            ForumPostModel.post_id.in_([second.post_id, third.post_id])
        ).update(
            {ForumPostModel.post_config: {"helpful": {"marked": True}}},
            synchronize_session=False,
        )

    first_page = repo.list_posts(topic_id=topic.topic_id, offset=0, limit=2)
    second_page = repo.list_posts(topic_id=topic.topic_id, offset=2, limit=2)

    assert first_page.total == 4
    assert [item.post_id for item in first_page.items] == [second.post_id, third.post_id]
    assert [item.floor_no for item in first_page.items] == [2, 3]
    assert [item.helpful for item in first_page.items] == [True, True]
    assert first_page.items[0].children[0].post_id == child.post_id
    assert first_page.items[0].children[0].reply_to_floor_no == 2
    assert first_page.items[0].children[0].helpful is False

    assert [item.post_id for item in second_page.items] == [first.post_id, fourth.post_id]
    assert [item.floor_no for item in second_page.items] == [1, 4]
    assert [item.helpful for item in second_page.items] == [False, False]


def test_list_topics_orders_by_latest_reply_activity(db):
    repo = ForumRepository(db)
    first = _topic(repo, "req-active-1", title="first")
    second = _topic(repo, "req-active-2", title="second")
    _reply(repo, first.topic_id, "req-active-reply", "reply revives first")

    with db.orm_session() as session:
        first_row = session.query(ForumTopicModel).filter_by(
            topic_id=first.topic_id
        ).one()
        second_row = session.query(ForumTopicModel).filter_by(
            topic_id=second.topic_id
        ).one()
        first_row.gmt_create = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
        second_row.gmt_create = datetime(2026, 9, 20, 8, 10, tzinfo=timezone.utc)
        session.query(ForumPostModel).filter_by(topic_id=first.topic_id).update(
            {ForumPostModel.gmt_create: datetime(2026, 9, 20, 8, 20, tzinfo=timezone.utc)}
        )

    result = repo.list_topics(keyword=None, status=None, offset=0, limit=20)

    assert [item.topic_id for item in result.items] == [first.topic_id, second.topic_id]
    assert result.items[0].latest_activity_at is not None
    assert result.items[1].latest_activity_at is not None


def test_get_topic_viewer_state_reports_current_vote_without_page_dependency(db):
    repo = ForumRepository(db)
    poll = repo.create_topic(
        author_type="HUMAN",
        author_id="author-human",
        client_request_id="viewer-poll-topic",
        title="Choose",
        body="Vote once",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(selection="SINGLE", options=("Option A", "Option B"))
        ),
    ).topic
    vote = repo.create_reply(
        topic_id=poll.topic_id,
        author_type="HUMAN",
        author_id="user-a",
        client_request_id="viewer-vote",
        body="I choose A",
        poll_selection=ForumPollSelection(option_indices=(0,)),
    ).post

    voted = repo.get_topic_viewer_state(
        topic_id=poll.topic_id, author_type="HUMAN", author_id="user-a"
    )
    not_voted = repo.get_topic_viewer_state(
        topic_id=poll.topic_id, author_type="HUMAN", author_id="user-b"
    )

    assert voted == ForumTopicViewerRecord(
        has_voted=True,
        vote_post_id=vote.post_id,
        poll_selection=ForumPollSelection(option_indices=(0,)),
    )
    assert not_voted == ForumTopicViewerRecord(has_voted=False)


def test_update_topic_tags_preserves_other_analysis_projection(db):
    from agentclaw.community.core.repository.implementations.task.forum import (
        ForumTopicTagRepository,
    )

    repo = ForumRepository(db)
    topic = _topic(repo, "tag-topic", title="tag topic")
    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).one()
        row.analysis_result = {
            "summary": {"content": "Existing summary"},
            "tags": [],
            "poll_cursor_id": 7,
        }

    updated = ForumTopicTagRepository(db).update_topic_tags(
        topic_id=topic.topic_id, tags=("工程", "架构")
    )

    assert updated is not None
    assert updated.analysis.tags == ("工程", "架构")
    assert updated.analysis.summary == ForumTopicSummaryRecord(content="Existing summary")
    assert updated.analysis.poll_result is None
    assert updated.analysis.poll_cursor_id == 7


def test_list_topics_counts_nested_replies_and_latest_child_activity(db):
    repo = ForumRepository(db)
    topic = _topic(repo, "topic", title="topic")
    first_floor = _reply(
        repo, topic.topic_id, "post-1", "first floor"
    )
    child = _reply(
        repo,
        topic.topic_id,
        "post-2",
        "child",
        reply_to_post_id=first_floor.post_id,
    )
    grandchild = _reply(
        repo,
        topic.topic_id,
        "post-3",
        "grandchild",
        reply_to_post_id=child.post_id,
    )

    result = repo.list_topics(keyword=None, status=None, offset=0, limit=20)

    assert result.items[0].reply_count == 3
    assert result.items[0].latest_activity_at and (
        result.items[0].latest_activity_at.replace(tzinfo=None)
        == grandchild.created_at.replace(tzinfo=None)
    )


def test_list_posts_allows_closed_topic_but_hides_cross_tenant_topic(db):
    repo = ForumRepository(db)
    with avernet_tenant_scope("tenant-a"):
        topic = _topic(repo, "topic", title="topic")
        reply = _reply(repo, topic.topic_id, "post", "reply")
        with db.orm_session() as session:
            session.query(ForumTopicModel).filter_by(
                topic_id=topic.topic_id
            ).one().status = TOPIC_STATUS_CLOSED
        result = repo.list_posts(topic_id=topic.topic_id, offset=0, limit=20)
        assert [item.post_id for item in result.items] == [reply.post_id]

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(NotFound, match="topic not found"):
            repo.list_posts(topic_id=topic.topic_id, offset=0, limit=20)
