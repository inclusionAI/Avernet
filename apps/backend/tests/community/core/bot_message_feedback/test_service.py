"""Tests for BotMessageFeedbackService."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.bot_message_feedback.schemas import BotMessageFeedbackRecord
from agentclaw.community.core.bot_message_feedback.service import BotMessageFeedbackService

pytestmark = pytest.mark.asyncio


def _record(
    message_id="msg-1",
    user_id="u1",
    feedback_type="like",
    message_content="answer",
    user_message_id="msg-0",
    user_message_content="question",
    session_key=None,
    bot_id="b1",
    reason=None,
    comment=None,
    env="dev",
):
    return BotMessageFeedbackRecord(
        id=1,
        message_id=message_id,
        message_content=message_content,
        user_message_id=user_message_id,
        user_message_content=user_message_content,
        session_key=session_key,
        bot_id=bot_id,
        user_id=user_id,
        feedback_type=feedback_type,
        reason=reason,
        comment=comment,
        env=env,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


async def test_submit_feedback_validates_required_fields():
    repo = AsyncMock()
    service = BotMessageFeedbackService(repo)

    with pytest.raises(ValueError, match="message_id is required"):
        await service.submit_feedback("", "u1", "like", "a", "b", "c", "d1")

    with pytest.raises(ValueError, match="message_id is required"):
        await service.submit_feedback("   ", "u1", "like", "a", "b", "c", "d1")

    with pytest.raises(ValueError, match="user_id is required"):
        await service.submit_feedback("msg-1", "", "like", "a", "b", "c", "d1")


async def test_submit_feedback_validates_feedback_type():
    repo = AsyncMock()
    service = BotMessageFeedbackService(repo)

    with pytest.raises(ValueError, match="feedback_type must be one of"):
        await service.submit_feedback("msg-1", "u1", "neutral", "a", "b", "c", "d1")


async def test_submit_feedback_calls_repo_upsert_with_qa_pair():
    repo = AsyncMock()
    repo.upsert.return_value = _record(
        message_id="msg-1",
        user_id="u1",
        feedback_type="dislike",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason="inaccurate",
        comment="bad",
    )
    service = BotMessageFeedbackService(repo)

    record = await service.submit_feedback(
        message_id="msg-1",
        user_id="u1",
        feedback_type="DISLIKE",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason="inaccurate",
        comment="bad",
    )

    assert record.feedback_type == "dislike"
    repo.upsert.assert_awaited_once_with(
        message_id="msg-1",
        user_id="u1",
        feedback_type="dislike",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason="inaccurate",
        comment="bad",
        env="dev",
    )


async def test_like_clears_reason_and_comment():
    repo = AsyncMock()
    repo.upsert.return_value = _record(
        message_id="msg-1",
        user_id="u1",
        feedback_type="like",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason=None,
        comment=None,
    )
    service = BotMessageFeedbackService(repo)

    record = await service.submit_feedback(
        message_id="msg-1",
        user_id="u1",
        feedback_type="like",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason="should-be-ignored",
        comment="should-be-ignored-too",
    )

    assert record.feedback_type == "like"
    repo.upsert.assert_awaited_once_with(
        message_id="msg-1",
        user_id="u1",
        feedback_type="like",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason=None,
        comment=None,
        env="dev",
    )




async def test_get_feedback_returns_none_when_repo_has_no_row():
    repo = AsyncMock()
    repo.list_by_message_ids.return_value = []
    service = BotMessageFeedbackService(repo)

    record = await service.get_feedback("msg-1", "u1")

    assert record is None
    repo.list_by_message_ids.assert_awaited_once_with(
        ["msg-1"], user_id="u1", env="dev"
    )


async def test_get_feedback_validates_identity_and_message_id():
    service = BotMessageFeedbackService(AsyncMock())

    with pytest.raises(ValueError, match="message_id is required"):
        await service.get_feedback(" ", "u1")
    with pytest.raises(ValueError, match="user_id is required"):
        await service.get_feedback("msg-1", "")


async def test_list_feedback_deduplicates_and_trims_ids():
    repo = AsyncMock()
    repo.list_by_message_ids.return_value = [_record(message_id="msg-1")]
    service = BotMessageFeedbackService(repo)

    records = await service.list_feedback([" msg-1 ", "msg-2", "msg-1", ""], "u1")

    assert [record.message_id for record in records] == ["msg-1"]
    repo.list_by_message_ids.assert_awaited_once_with(
        ["msg-1", "msg-2"], user_id="u1", env="dev"
    )


async def test_list_feedback_rejects_empty_and_oversized_batches():
    service = BotMessageFeedbackService(AsyncMock())

    with pytest.raises(ValueError, match="message_ids is required"):
        await service.list_feedback([" ", ""], "u1")
    with pytest.raises(ValueError, match="at most 100 items"):
        await service.list_feedback(
            [f"msg-{index}" for index in range(101)], "u1"
        )
