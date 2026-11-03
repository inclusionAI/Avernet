from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.errors import Conflict, NotFound
from agentclaw.community.core.task.forum.models import (
    ForumPollConfig,
    ForumPollSelection,
    ForumTopicConfig,
    TOPIC_STATUS_CLOSED,
    TOPIC_STATUS_LOCKED,
    TOPIC_STATUS_OPEN,
    TOPIC_TYPE_HELP,
    TOPIC_TYPE_NOTICE,
    TOPIC_TYPE_POLL,
    TOPIC_TYPE_SHARE,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumInteractionModel,
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    ForumRepository,
    _is_post_request_idempotency_conflict,
    _is_topic_request_idempotency_conflict,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def _create_topic(
    repo,
    *,
    author_type="BOT",
    author_id="bot-a",
    request_id="req-topic",
    topic_type="DISCUSSION",
    topic_config=None,
):
    return repo.create_topic(
        author_type=author_type,
        author_id=author_id,
        client_request_id=request_id,
        title="Topic title",
        body="Topic description",
        topic_type=topic_type,
        topic_config=topic_config,
    )


def _create_reply(
    repo,
    topic_id,
    *,
    author_type="BOT",
    author_id="bot-b",
    request_id="req-reply-1",
    body="First reply",
    reply_to_post_id=None,
    poll_selection=None,
):
    return repo.create_reply(
        topic_id=topic_id,
        author_type=author_type,
        author_id=author_id,
        client_request_id=request_id,
        body=body,
        reply_to_post_id=reply_to_post_id,
        poll_selection=poll_selection,
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


def test_create_topic_persists_and_returns_poll_configuration(db):
    repo = ForumRepository(db)
    deadline = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    config = ForumTopicConfig(
        poll=ForumPollConfig(
            selection="MULTI",
            options=("Option A", "Option B"),
            deadline_at=deadline,
        )
    )

    result = _create_topic(
        repo,
        request_id="req-poll",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=config,
    )

    assert result.topic.topic_config == config
    assert repo.get_topic(result.topic.topic_id).topic_config == config
    listed = repo.list_topics(
        keyword=None, status=None, topic_type=TOPIC_TYPE_POLL, offset=0, limit=10
    )
    assert listed.items[0].topic_config == config

    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(
            topic_id=result.topic.topic_id
        ).one()
        assert row.topic_config == {
            "poll": {
                "selection": "MULTI",
                "options": ["Option A", "Option B"],
                "deadline_at": deadline.isoformat(),
            }
        }


def test_create_share_topic_without_configuration(db):
    result = _create_topic(
        ForumRepository(db),
        request_id="req-share",
        topic_type=TOPIC_TYPE_SHARE,
        topic_config=None,
    )

    assert result.created is True
    assert result.topic.topic_config is None


def test_create_topic_poll_replay_returns_original_configuration(db):
    repo = ForumRepository(db)
    poll = ForumPollConfig(selection="SINGLE", options=("A", "B"))
    topic_config = ForumTopicConfig(poll=poll)
    first = _create_topic(
        repo,
        request_id="req-poll",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=topic_config,
    )

    replay = _create_topic(
        repo,
        request_id="req-poll",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=None,
    )

    assert replay.created is False
    assert replay.topic.topic_id == first.topic.topic_id
    assert replay.topic.topic_config.poll == poll


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
    assert result.post.reply_to_post_id is None
    assert "env" not in ForumPostModel.__table__.columns
    assert "avernet_tenant" not in ForumPostModel.__table__.columns


def test_create_reply_persists_reference_to_a_post_in_the_same_topic(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic
    first = _create_reply(repo, topic.topic_id).post

    second = _create_reply(
        repo,
        topic.topic_id,
        request_id="reply-to-first",
        body="Referenced reply",
        reply_to_post_id=first.post_id,
    )

    assert second.post.reply_to_post_id == first.post_id
    with db.orm_session() as session:
        row = session.query(ForumPostModel).filter_by(post_id=second.post.post_id).one()
        assert row.reply_to_post_id == first.post_id


def test_create_reply_persists_structured_poll_selection(db):
    repo = ForumRepository(db)
    config = ForumTopicConfig(
        poll=ForumPollConfig(selection="MULTI", options=("Option A", "Option B"))
    )
    topic = _create_topic(
        repo,
        request_id="req-poll",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=config,
    ).topic

    result = _create_reply(
        repo,
        topic.topic_id,
        request_id="req-poll-vote",
        body="I choose both options.",
        poll_selection=ForumPollSelection(option_indices=(0, 1)),
    )

    assert result.post.poll_selection == ForumPollSelection(option_indices=(0, 1))
    with db.orm_session() as session:
        row = session.query(ForumPostModel).filter_by(post_id=result.post.post_id).one()
        assert row.post_config == {
            "poll_selection": {"option_indices": [0, 1]}
        }
        assert row.body == "I choose both options."


def test_create_reply_rejects_unknown_reference(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic

    with pytest.raises(NotFound, match="reply_to_post_id not found"):
        _create_reply(repo, topic.topic_id, reply_to_post_id="missing-post")

    with db.orm_session() as session:
        assert session.query(ForumPostModel).count() == 0


def test_poll_reply_rejects_floor_reference_before_validation(db):
    repo = ForumRepository(db)
    topic = _create_topic(
        repo,
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(selection="SINGLE", options=("A", "B"))
        ),
    ).topic

    with pytest.raises(Conflict, match="POLL replies must target the Topic"):
        _create_reply(
            repo,
            topic.topic_id,
            reply_to_post_id="missing-post",
            poll_selection=ForumPollSelection(option_indices=(0,)),
        )

    with db.orm_session() as session:
        assert session.query(ForumPostModel).count() == 0


def test_poll_reply_rejects_new_vote_after_deadline(db):
    repo = ForumRepository(db)
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    topic = _create_topic(
        repo,
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(
                selection="SINGLE",
                options=("A", "B"),
                deadline_at=expired,
            )
        ),
    ).topic

    with pytest.raises(Conflict, match="poll deadline has passed"):
        _create_reply(
            repo,
            topic.topic_id,
            poll_selection=ForumPollSelection(option_indices=(0,)),
        )

    with db.orm_session() as session:
        assert session.query(ForumPostModel).count() == 0


def test_poll_deadline_does_not_break_idempotent_reply_replay(db):
    repo = ForumRepository(db)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    topic = _create_topic(
        repo,
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(
                selection="SINGLE",
                options=("A", "B"),
                deadline_at=future,
            )
        ),
    ).topic
    first = _create_reply(
        repo,
        topic.topic_id,
        poll_selection=ForumPollSelection(option_indices=(0,)),
    ).post

    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(
            topic_id=topic.topic_id
        ).one()
        row.topic_config = {
            "poll": {
                "selection": "SINGLE",
                "options": ["A", "B"],
                "deadline_at": expired.isoformat(),
            }
        }

    replay = _create_reply(
        repo,
        topic.topic_id,
        body="ignored",
        reply_to_post_id="missing-post",
        poll_selection=ForumPollSelection(option_indices=(0,)),
    )

    assert replay.created is False
    assert replay.post.post_id == first.post_id


def test_create_reply_rejects_reference_from_another_topic(db):
    repo = ForumRepository(db)
    first_topic = _create_topic(repo, request_id="topic-1").topic
    second_topic = _create_topic(repo, request_id="topic-2").topic
    first_reply = _create_reply(repo, first_topic.topic_id).post

    with pytest.raises(NotFound, match="reply_to_post_id not found"):
        _create_reply(
            repo,
            second_topic.topic_id,
            reply_to_post_id=first_reply.post_id,
        )


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


@pytest.mark.parametrize("topic_type", [TOPIC_TYPE_POLL, TOPIC_TYPE_NOTICE])
def test_poll_and_notice_allow_only_one_reply_per_author(db, topic_type):
    repo = ForumRepository(db)
    topic = _create_topic(repo, topic_type=topic_type).topic

    first = _create_reply(repo, topic.topic_id, request_id="reply-1")

    with pytest.raises(Conflict, match="already replied"):
        _create_reply(repo, topic.topic_id, request_id="reply-2")

    replay = _create_reply(repo, topic.topic_id, request_id="reply-1", body="ignored")
    assert replay.created is False
    assert replay.post.post_id == first.post.post_id


def test_poll_allows_different_authors_to_reply_once(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo, topic_type=TOPIC_TYPE_POLL).topic

    bot_reply = _create_reply(repo, topic.topic_id, author_id="bot-b")
    human_reply = _create_reply(
        repo,
        topic.topic_id,
        author_type="HUMAN",
        author_id="user-b",
        request_id="human-reply",
    )

    assert bot_reply.post.post_id != human_reply.post.post_id


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


def test_idempotent_reply_replay_skips_reference_validation_after_topic_is_locked(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic
    first = _create_reply(repo, topic.topic_id).post
    with db.orm_session() as session:
        session.query(ForumTopicModel).filter_by(
            topic_id=topic.topic_id
        ).one().status = TOPIC_STATUS_LOCKED

    replay = _create_reply(
        repo,
        topic.topic_id,
        body="ignored",
        reply_to_post_id="missing-post",
    )

    assert replay.created is False
    assert replay.post.post_id == first.post_id


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


# ---------------------------------------------------------------------------
# update_topic_status
# ---------------------------------------------------------------------------


def test_update_topic_status_persists_new_status_and_returns_refreshed_record(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic

    refreshed = repo.update_topic_status(
        topic_id=topic.topic_id, status=TOPIC_STATUS_CLOSED
    )

    assert refreshed is not None
    assert refreshed.status == TOPIC_STATUS_CLOSED
    assert refreshed.topic_id == topic.topic_id
    assert refreshed.title == topic.title

    # And the change is durable: a fresh read sees the same status.
    reread = repo.get_topic(topic.topic_id)
    assert reread is not None
    assert reread.status == TOPIC_STATUS_CLOSED


def test_update_topic_status_returns_none_for_unknown_topic(db):
    repo = ForumRepository(db)

    # An empty tenant has no topics at all, so a stray id returns None and
    # persists nothing — the empty-transaction early return is what the service
    # maps back to NotFound when a concurrent delete races a read-then-write.
    assert repo.update_topic_status(topic_id="topic-absent", status=TOPIC_STATUS_CLOSED) is None
    assert repo.get_topic("topic-absent") is None


def test_update_topic_status_transitions_closed_back_to_open(db):
    # The repository sets whatever status the service asks; the service owns the
    # transition rules. Locking a closed topic from here proves the writer does
    # not consult the prior status before writing the new one.
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic

    repo.update_topic_status(topic_id=topic.topic_id, status=TOPIC_STATUS_CLOSED)
    locked = repo.update_topic_status(
        topic_id=topic.topic_id, status=TOPIC_STATUS_LOCKED
    )

    assert locked is not None
    assert locked.status == TOPIC_STATUS_LOCKED


# ---------------------------------------------------------------------------
# update_topic_content / delete_topic
# ---------------------------------------------------------------------------


def test_update_topic_content_persists_only_open_topic_content(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic

    updated = repo.update_topic_content(
        topic_id=topic.topic_id,
        title="Edited title",
        body="Edited description",
        expected_status=TOPIC_STATUS_OPEN,
    )

    assert updated is not None
    assert updated.title == "Edited title"
    assert updated.body == "Edited description"
    reread = repo.get_topic(topic.topic_id)
    assert reread is not None
    assert (reread.title, reread.body) == ("Edited title", "Edited description")


def test_update_topic_content_returns_none_when_status_no_longer_matches(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic
    repo.update_topic_status(topic_id=topic.topic_id, status=TOPIC_STATUS_CLOSED)

    assert repo.update_topic_content(
        topic_id=topic.topic_id,
        title="Edited title",
        body="Edited description",
        expected_status=TOPIC_STATUS_OPEN,
    ) is None


def test_delete_topic_removes_topic_posts_and_direct_interactions(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic
    post = _create_reply(repo, topic.topic_id).post
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    with db.orm_session() as session:
        session.add_all(
            [
                ForumInteractionModel(
                    actor_type="HUMAN",
                    actor_id="user-a",
                    action_type="VIEW",
                    target_type="TOPIC",
                    target_id=topic.topic_id,
                    env="dev",
                    gmt_create=now,
                    gmt_modified=now,
                ),
                ForumInteractionModel(
                    actor_type="HUMAN",
                    actor_id="user-a",
                    action_type="LIKE",
                    target_type="POST",
                    target_id=post.post_id,
                    env="dev",
                    gmt_create=now,
                    gmt_modified=now,
                ),
                ForumInteractionModel(
                    actor_type="HUMAN",
                    actor_id="user-a",
                    action_type="FOLLOW",
                    target_type="HUMAN",
                    target_id="user-keep",
                    env="dev",
                    gmt_create=now,
                    gmt_modified=now,
                ),
            ]
        )

    assert repo.delete_topic(topic.topic_id) is True

    with db.orm_session() as session:
        assert session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).count() == 0
        assert session.query(ForumPostModel).filter_by(topic_id=topic.topic_id).count() == 0
        assert session.query(ForumInteractionModel).count() == 1


def test_delete_topic_refuses_locked_topic_and_returns_false(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo).topic
    post = _create_reply(repo, topic.topic_id).post
    repo.update_topic_status(topic_id=topic.topic_id, status=TOPIC_STATUS_LOCKED)

    assert repo.delete_topic(topic.topic_id) is False

    with db.orm_session() as session:
        assert session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).count() == 1
        assert session.query(ForumPostModel).filter_by(post_id=post.post_id).count() == 1


# ---------------------------------------------------------------------------
# HELP reply helpful marker
# ---------------------------------------------------------------------------


def test_update_post_helpful_persists_marker_and_clear_idempotently(db):
    repo = ForumRepository(db)
    topic = _create_topic(repo, topic_type=TOPIC_TYPE_HELP).topic
    post = _create_reply(repo, topic.topic_id).post

    marked = repo.update_post_helpful(
        topic_id=topic.topic_id, post_id=post.post_id, helpful=True
    )
    marked_again = repo.update_post_helpful(
        topic_id=topic.topic_id, post_id=post.post_id, helpful=True
    )

    assert marked is not None and marked.helpful is True
    assert marked_again is not None and marked_again.helpful is True
    with db.orm_session() as session:
        row = session.query(ForumPostModel).filter_by(post_id=post.post_id).one()
        assert row.post_config == {"helpful": {"marked": True}}

    cleared = repo.update_post_helpful(
        topic_id=topic.topic_id, post_id=post.post_id, helpful=False
    )
    cleared_again = repo.update_post_helpful(
        topic_id=topic.topic_id, post_id=post.post_id, helpful=False
    )

    assert cleared is not None and cleared.helpful is False
    assert cleared_again is not None and cleared_again.helpful is False
    with db.orm_session() as session:
        row = session.query(ForumPostModel).filter_by(post_id=post.post_id).one()
        assert row.post_config is None
        sql_null_count = session.execute(
            text(
                "SELECT COUNT(*) FROM ac_forum_post "
                "WHERE post_id = :post_id AND post_config IS NULL"
            ),
            {"post_id": post.post_id},
        ).scalar_one()
        assert sql_null_count == 1


def test_get_and_update_post_are_scoped_by_topic_tenant_and_environment(db, monkeypatch):
    repo = ForumRepository(db)
    with avernet_tenant_scope("tenant-a"):
        first_topic = _create_topic(repo, request_id="help-topic-a", topic_type=TOPIC_TYPE_HELP).topic
        second_topic = _create_topic(repo, request_id="help-topic-b", topic_type=TOPIC_TYPE_HELP).topic
        post = _create_reply(repo, first_topic.topic_id).post

        assert repo.get_post(topic_id=first_topic.topic_id, post_id=post.post_id) is not None
        assert repo.get_post(topic_id=second_topic.topic_id, post_id=post.post_id) is None
        assert (
            repo.update_post_helpful(
                topic_id=second_topic.topic_id,
                post_id=post.post_id,
                helpful=True,
            )
            is None
        )

    with avernet_tenant_scope("tenant-b"):
        assert repo.get_post(topic_id=first_topic.topic_id, post_id=post.post_id) is None
        assert (
            repo.update_post_helpful(
                topic_id=first_topic.topic_id,
                post_id=post.post_id,
                helpful=True,
            )
            is None
        )

    monkeypatch.setenv("SERVER_ENV", "pre")
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_post(topic_id=first_topic.topic_id, post_id=post.post_id) is None
        assert (
            repo.update_post_helpful(
                topic_id=first_topic.topic_id,
                post_id=post.post_id,
                helpful=True,
            )
            is None
        )

    monkeypatch.delenv("SERVER_ENV", raising=False)
    with avernet_tenant_scope("tenant-a"):
        reread = repo.get_post(topic_id=first_topic.topic_id, post_id=post.post_id)
        assert reread is not None and reread.helpful is False
