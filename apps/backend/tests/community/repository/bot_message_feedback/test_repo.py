"""Tests for BotMessageFeedbackRepository."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_message_feedback.models import AcBotMessageFeedback
from agentclaw.community.core.repository.implementations.bot_message_feedback.repo import (
    BotMessageFeedbackRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

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

    session = orm_session


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bot_message_feedback.db'}",
        connect_args={"check_same_thread": False},
    )
    AcBotMessageFeedback.__table__.create(engine)
    return BotMessageFeedbackRepository(_FileSqliteDB(engine))


async def test_upsert_inserts_and_updates(repo):
    record = await repo.upsert(
        message_id="msg-1",
        user_id="u1",
        feedback_type="like",
        message_content="answer content",
        user_message_id="msg-0",
        user_message_content="question content",
        session_key="session-1",
        bot_id="bot-1",
        env="pre",
    )
    assert record.message_id == "msg-1"
    assert record.user_id == "u1"
    assert record.feedback_type == "like"
    assert record.message_content == "answer content"
    assert record.user_message_id == "msg-0"
    assert record.user_message_content == "question content"
    assert record.session_key == "session-1"
    assert record.bot_id == "bot-1"
    assert record.env == "pre"

    updated = await repo.upsert(
        message_id="msg-1",
        user_id="u1",
        feedback_type="dislike",
        message_content="updated answer",
        user_message_id="msg-0",
        user_message_content="question content",
        bot_id="bot-1",
        reason="inaccurate",
        comment="not helpful",
        env="pre",
    )
    assert updated.id == record.id
    assert updated.feedback_type == "dislike"
    assert updated.message_content == "updated answer"
    assert updated.user_message_id == "msg-0"
    assert updated.user_message_content == "question content"
    assert updated.reason == "inaccurate"
    assert updated.comment == "not helpful"
    assert updated.env == "pre"


async def test_upsert_is_isolated_by_env(repo):
    pre_record = await repo.upsert(
        message_id="msg-1",
        user_id="u1",
        feedback_type="like",
        message_content="answer content",
        user_message_id="msg-0",
        user_message_content="question content",
        session_key="session-1",
        bot_id="bot-1",
        env="pre",
    )

    prod_record = await repo.upsert(
        message_id="msg-1",
        user_id="u1",
        feedback_type="dislike",
        message_content="updated answer",
        user_message_id="msg-0",
        user_message_content="question content",
        bot_id="bot-1",
        reason="inaccurate",
        comment="not helpful",
        env="prod",
    )

    # Different env -> separate rows, no overwrite.
    assert prod_record.id != pre_record.id
    assert prod_record.env == "prod"
    assert prod_record.feedback_type == "dislike"
    assert pre_record.feedback_type == "like"

    # Updating the prod row again should not touch the pre row.
    prod_updated = await repo.upsert(
        message_id="msg-1",
        user_id="u1",
        feedback_type="like",
        message_content="updated answer 2",
        user_message_id="msg-0",
        user_message_content="question content",
        bot_id="bot-1",
        env="prod",
    )
    assert prod_updated.id == prod_record.id
    assert prod_updated.feedback_type == "like"


async def test_like_clears_reason_and_comment(repo):
    # 先点踩，写入 reason/comment
    await repo.upsert(
        "msg-1", "u1", "dislike",
        message_content="answer", user_message_id="msg-0",
        user_message_content="question", bot_id="bot-1",
        reason="inaccurate", comment="bad", env="dev",
    )
    # 再点赞时，repo 层应主动清空 reason/comment，避免数据污脏
    updated = await repo.upsert(
        "msg-1", "u1", "like",
        message_content="answer", user_message_id="msg-0",
        user_message_content="question", bot_id="bot-1",
        env="dev",
    )
    assert updated.feedback_type == "like"
    assert updated.reason is None
    assert updated.comment is None
    assert updated.message_content == "answer"
    assert updated.user_message_content == "question"
    assert updated.env == "dev"

