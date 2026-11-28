"""REST router for bot-output message feedback."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.bot_message_feedback.schemas import (
    BotMessageFeedbackResponse,
    SubmitFeedbackRequest,
)
from agentclaw.community.api.bot_message_feedback_service import (
    BotMessageFeedbackServiceProtocol,
)
from agentclaw.community.core.bot_chat.schemas import ApiResponse
from agentclaw.community.di import Injected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bot-message-feedback", tags=["bot-message-feedback"])


def _record_to_response(record) -> BotMessageFeedbackResponse:
    return BotMessageFeedbackResponse(
        id=record.id,
        message_id=record.message_id,
        message_content=record.message_content,
        user_message_id=record.user_message_id,
        user_message_content=record.user_message_content,
        session_key=record.session_key,
        bot_id=record.bot_id,
        user_id=record.user_id,
        feedback_type=record.feedback_type,
        reason=record.reason,
        comment=record.comment,
        env=record.env,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post(
    "/{message_id}",
    response_model=ApiResponse[BotMessageFeedbackResponse],
)
async def submit_message_feedback(
    message_id: str,
    req: SubmitFeedbackRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotMessageFeedbackServiceProtocol = Injected(BotMessageFeedbackServiceProtocol),
):
    try:
        record = await service.submit_feedback(
            message_id=message_id,
            user_id=user.staffId,
            feedback_type=req.feedback_type,
            message_content=req.message_content,
            user_message_id=req.user_message_id,
            user_message_content=req.user_message_content,
            session_key=req.session_key,
            bot_id=req.bot_id,
            reason=req.reason,
            comment=req.comment,
        )
        return ApiResponse(success=True, message="ok", data=_record_to_response(record))
    except ValueError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=4000,
            data=None,
        )
    except Exception as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=5999,
            data=None,
        )


@router.get(
    "",
    response_model=ApiResponse[list[BotMessageFeedbackResponse]],
)
async def list_message_feedback(
    message_ids: list[str] = Query(
        ...,
        description=(
            "Message ids to read. Repeat the parameter or pass a comma-separated "
            "value; at most 100 distinct ids."
        ),
    ),
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotMessageFeedbackServiceProtocol = Injected(BotMessageFeedbackServiceProtocol),
):
    """Return the current user's feedback for a batch of bot messages.

    Missing ids are omitted, so a history page always gets a 200 even when most
    messages have no feedback yet.
    """
    requested: list[str] = []
    for raw in message_ids:
        requested.extend(part.strip() for part in (raw or "").split(","))
    requested = [message_id for message_id in requested if message_id]

    try:
        records = await service.list_feedback(
            message_ids=requested,
            user_id=user.staffId,
        )
        return ApiResponse(
            success=True,
            message="ok",
            data=[_record_to_response(record) for record in records],
        )
    except ValueError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=4000,
            data=None,
        )
    except Exception:
        logger.exception("Failed to list bot message feedback")
        return ApiResponse(
            success=False,
            message="读取反馈失败，请稍后重试",
            error_code=5999,
            data=None,
        )


@router.get(
    "/{message_id}",
    response_model=ApiResponse[BotMessageFeedbackResponse],
)
async def get_message_feedback(
    message_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotMessageFeedbackServiceProtocol = Injected(BotMessageFeedbackServiceProtocol),
):
    """Return the current user's feedback for one message, or ``null``."""
    try:
        record = await service.get_feedback(
            message_id=message_id,
            user_id=user.staffId,
        )
        return ApiResponse(
            success=True,
            message="ok",
            data=_record_to_response(record) if record is not None else None,
        )
    except ValueError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=4000,
            data=None,
        )
    except Exception:
        logger.exception("Failed to read bot message feedback")
        return ApiResponse(
            success=False,
            message="读取反馈失败，请稍后重试",
            error_code=5999,
            data=None,
        )
