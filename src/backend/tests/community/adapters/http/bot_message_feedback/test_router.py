"""Tests for bot_message_feedback router."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.adapters.http.bot_message_feedback.router import (
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
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def test_router_routes():
    paths = {route.path for route in router.routes}
    assert paths == {"/api/v1/bot-message-feedback/{message_id}"}


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

