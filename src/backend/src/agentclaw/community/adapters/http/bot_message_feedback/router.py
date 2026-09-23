"""REST router for bot-output message feedback."""
from __future__ import annotations

from fastapi import APIRouter, Depends

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


