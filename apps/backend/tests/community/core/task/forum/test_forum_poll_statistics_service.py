from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import agentclaw.community.core.task.forum.repository.models  # noqa: F401
from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    ForumRepository,
)
from agentclaw.community.core.task.forum.models import (
    TOPIC_STATUS_CLOSED,
    TOPIC_STATUS_OPEN,
    TOPIC_TYPE_POLL,
    ForumPollConfig,
    ForumPollSelection,
    ForumPollStatisticsResult,
    ForumTopicConfig,
)
from agentclaw.community.core.task.forum.repository.models import ForumTopicModel
from agentclaw.community.core.task.forum.services.poll_statistics_service import (
    ForumPollStatisticsService,
)


class InMemorySqliteDB:
    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def transactional_orm_session(self):
        return self.orm_session()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return InMemorySqliteDB(engine)


def _poll_topic(repo):
    return repo.create_topic(
        author_type="HUMAN",
        author_id="author-human",
        client_request_id="req-poll-topic",
        title="Choose a direction",
        body="Please vote and explain your choice.",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(selection="SINGLE", options=("Option A", "Option B")),
        ),
    ).topic


def _vote(repo, topic_id, *, author_id, request_id, option_index):
    return repo.create_reply(
        topic_id=topic_id,
        author_type="HUMAN",
        author_id=author_id,
        client_request_id=request_id,
        body="I choose Option %s." % ("A" if option_index == 0 else "B"),
        poll_selection=ForumPollSelection(option_indices=(option_index,)),
    ).post


def test_poll_statistics_service_increments_and_resumes_by_post_cursor(db):
    repo = ForumRepository(db)
    topic = _poll_topic(repo)
    _vote(
        repo, topic.topic_id, author_id="voter-a", request_id="vote-a", option_index=0
    )

    service = ForumPollStatisticsService(repository=repo)
    first = service.execute()

    assert first == ForumPollStatisticsResult(topics_processed=1, posts_processed=1)
    fresh = repo.get_topic(topic.topic_id)
    assert fresh.analysis.poll_result is not None
    assert fresh.analysis.poll_result.total_votes == 1
    assert fresh.analysis.poll_result.participant_count == 1
    first_cursor = fresh.analysis.poll_cursor_id
    assert first_cursor > 0

    _vote(
        repo, topic.topic_id, author_id="voter-b", request_id="vote-b", option_index=1
    )

    second = service.execute()

    assert second == ForumPollStatisticsResult(topics_processed=1, posts_processed=1)
    updated = repo.get_topic(topic.topic_id)
    assert updated.analysis.poll_result is not None
    assert updated.analysis.poll_result.total_votes == 2
    assert updated.analysis.poll_result.participant_count == 2
    assert updated.analysis.poll_cursor_id > first_cursor
    assert [
        (o.index, o.votes, o.percent) for o in updated.analysis.poll_result.options
    ] == [
        (0, 1, 50),
        (1, 1, 50),
    ]


def test_poll_statistics_service_empty_batch_does_not_rewrite_topic(db, monkeypatch):
    repo = ForumRepository(db)
    topic = _poll_topic(repo)
    service = ForumPollStatisticsService(repository=repo)
    service.execute()

    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).one()
        modified_after_first_call = row.gmt_modified

    class LaterDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2030, 1, 1, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(
        "agentclaw.community.core.repository.implementations.task.forum.forum_repository.datetime",
        LaterDatetime,
    )
    repeat = service.execute()

    assert repeat == ForumPollStatisticsResult(topics_processed=1, posts_processed=0)
    with db.orm_session() as session:
        row = session.query(ForumTopicModel).filter_by(topic_id=topic.topic_id).one()
        assert row.gmt_modified == modified_after_first_call


def test_poll_statistics_service_counts_each_multi_selection_vote(db):
    repo = ForumRepository(db)
    topic = repo.create_topic(
        author_type="HUMAN",
        author_id="author-human",
        client_request_id="req-multi-poll",
        title="Choose directions",
        body="Select every option you support.",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(selection="MULTI", options=("Option A", "Option B")),
        ),
    ).topic
    repo.create_reply(
        topic_id=topic.topic_id,
        author_type="HUMAN",
        author_id="voter-a",
        client_request_id="vote-multi-a",
        body="I support both options.",
        poll_selection=ForumPollSelection(option_indices=(0, 1)),
    )

    result = ForumPollStatisticsService(repository=repo).execute()

    assert result == ForumPollStatisticsResult(topics_processed=1, posts_processed=1)
    persisted = repo.get_topic(topic.topic_id)
    assert persisted.analysis.poll_result is not None
    assert persisted.analysis.poll_result.total_votes == 2
    assert persisted.analysis.poll_result.participant_count == 1
    assert [
        (o.index, o.votes, o.percent) for o in persisted.analysis.poll_result.options
    ] == [
        (0, 1, 50),
        (1, 1, 50),
    ]


def test_poll_statistics_service_processes_only_new_posts_on_repeat(db):
    repo = ForumRepository(db)
    topic = _poll_topic(repo)
    _vote(
        repo, topic.topic_id, author_id="voter-a", request_id="vote-a", option_index=0
    )
    service = ForumPollStatisticsService(repository=repo)

    service.execute()
    repeat = service.execute()

    assert repeat == ForumPollStatisticsResult(topics_processed=1, posts_processed=0)
    persisted = repo.get_topic(topic.topic_id)
    assert persisted.analysis.poll_result is not None
    assert persisted.analysis.poll_result.total_votes == 1
    assert persisted.analysis.poll_result.participant_count == 1


def test_poll_statistics_task_closes_expired_poll_topic(db):
    repo = ForumRepository(db)
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    topic = repo.create_topic(
        author_type="HUMAN",
        author_id="author-human",
        client_request_id="req-expired-poll-topic",
        title="Expired poll",
        body="Vote before the deadline.",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(
                selection="SINGLE",
                options=("Option A", "Option B"),
                deadline_at=expired,
            )
        ),
    ).topic

    result = ForumPollStatisticsService(repository=repo).execute()

    assert result == ForumPollStatisticsResult(
        topics_processed=1,
        posts_processed=0,
        topics_closed=1,
    )
    assert repo.get_topic(topic.topic_id).status == TOPIC_STATUS_CLOSED


def test_poll_statistics_task_leaves_future_poll_topic_open(db):
    repo = ForumRepository(db)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    topic = repo.create_topic(
        author_type="HUMAN",
        author_id="author-human",
        client_request_id="req-future-poll-topic",
        title="Future poll",
        body="Vote at any time.",
        topic_type=TOPIC_TYPE_POLL,
        topic_config=ForumTopicConfig(
            poll=ForumPollConfig(
                selection="SINGLE",
                options=("Option A", "Option B"),
                deadline_at=future,
            )
        ),
    ).topic

    result = ForumPollStatisticsService(repository=repo).execute()

    assert result == ForumPollStatisticsResult(
        topics_processed=1,
        posts_processed=0,
        topics_closed=0,
    )
    assert repo.get_topic(topic.topic_id).status == TOPIC_STATUS_OPEN
