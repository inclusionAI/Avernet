"""Tests for bot_message_feedback router."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.adapters.http.bot_message_feedback.router import (
    get_message_feedback,
    list_message_feedback,
    router,
    submit_message_feedback,
)
from agentclaw.community.adapters.http.bot_message_feedback.schemas import (
    SubmitFeedbackRequest,
)
from agentclaw.community.core.bot_message_feedback.schemas import BotMessageFeedbackRecord

pytestmark = pytest.mark.asyncio


def _record(
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
        message_id="msg-1",
        message_content=message_content,
        user_message_id=user_message_id,
        user_message_content=user_message_content,
        session_key=session_key,
        bot_id=bot_id,
        user_id="u1",
        feedback_type=feedback_type,
        reason=reason,
        comment=comment,
        env=env,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def test_router_routes():
    paths = {route.path for route in router.routes}
    assert paths == {
        "/api/v1/bot-message-feedback",
        "/api/v1/bot-message-feedback/{message_id}",
    }


async def test_submit_feedback_returns_success():
    service = AsyncMock()
    service.submit_feedback.return_value = _record(
        feedback_type="dislike",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason="inaccurate",
        comment="not helpful",
    )
    user = SimpleNamespace(staffId="u1")
    request = SubmitFeedbackRequest(
        feedback_type="dislike",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason="inaccurate",
        comment="not helpful",
    )

    response = await submit_message_feedback(
        message_id="msg-1",
        req=request,
        user=user,
        service=service,
    )

    assert response.success is True
    assert response.data.message_id == "msg-1"
    assert response.data.feedback_type == "dislike"
    assert response.data.message_content == "answer"
    assert response.data.user_message_id == "msg-0"
    assert response.data.user_message_content == "question"
    assert response.data.env == "dev"
    service.submit_feedback.assert_awaited_once_with(
        message_id="msg-1",
        user_id="u1",
        feedback_type="dislike",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        session_key="s1",
        bot_id="b1",
        reason="inaccurate",
        comment="not helpful",
    )


async def test_submit_feedback_handles_validation_error():
    service = AsyncMock()
    service.submit_feedback.side_effect = ValueError("bad input")
    user = SimpleNamespace(staffId="u1")
    request = SubmitFeedbackRequest(
        feedback_type="like",
        message_content="answer",
        user_message_id="msg-0",
        user_message_content="question",
        bot_id="b1",
    )

    response = await submit_message_feedback(
        message_id="msg-1",
        req=request,
        user=user,
        service=service,
    )

    assert response.success is False
    assert response.error_code == 4000
    assert "bad input" in response.message


async def test_get_feedback_returns_record():
    service = AsyncMock()
    service.get_feedback.return_value = _record(feedback_type="like")
    user = SimpleNamespace(staffId="u1")

    response = await get_message_feedback(
        message_id="msg-1",
        user=user,
        service=service,
    )

    assert response.success is True
    assert response.data.message_id == "msg-1"
    assert response.data.feedback_type == "like"
    service.get_feedback.assert_awaited_once_with(message_id="msg-1", user_id="u1")


async def test_get_feedback_returns_null_when_absent():
    service = AsyncMock()
    service.get_feedback.return_value = None
    user = SimpleNamespace(staffId="u1")

    response = await get_message_feedback(
        message_id="msg-1",
        user=user,
        service=service,
    )

    assert response.success is True
    assert response.data is None


async def test_list_feedback_splits_and_trims_message_ids():
    service = AsyncMock()
    service.list_feedback.return_value = [_record(feedback_type="dislike")]
    user = SimpleNamespace(staffId="u1")

    response = await list_message_feedback(
        message_ids=["msg-1, msg-2", "msg-3"],
        user=user,
        service=service,
    )

    assert response.success is True
    assert [item.message_id for item in response.data] == ["msg-1"]
    service.list_feedback.assert_awaited_once_with(
        message_ids=["msg-1", "msg-2", "msg-3"],
        user_id="u1",
    )


async def test_list_feedback_handles_validation_error():
    service = AsyncMock()
    service.list_feedback.side_effect = ValueError("message_ids is required")
    user = SimpleNamespace(staffId="u1")

    response = await list_message_feedback(
        message_ids=[", "],
        user=user,
        service=service,
    )

    assert response.success is False
    assert response.error_code == 4000
    assert "message_ids is required" in response.message
