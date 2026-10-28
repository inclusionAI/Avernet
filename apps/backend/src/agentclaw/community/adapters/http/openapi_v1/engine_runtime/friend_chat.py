"""Explicit BCN-authorized Human-to-Agent routing for engine-runtime APIs."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import Query, Request

from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.core.bot_chat.bcn_friendship import (
    FriendshipSourceUnavailableError,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
)
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError, BotNotActiveError, BotNotPublishedError,
    ChatPermissionError, ConnectionError,
)

FRIEND_USER_ID_DESCRIPTION = (
    "The Human friend whose caller-owned chat resources are addressed. Omit "
    "this parameter to preserve the existing owner/collaborator Engine Runtime "
    "behavior. When supplied, BCN friendship is authoritative; the caller must "
    "be this Human or the Bot owner. Service Bot conversations address online "
    "(legacy draft selectors also mean online); personal Bots address draft."
)

FriendUserIdQuery = Annotated[
    str | None,
    Query(alias="f_user_id", min_length=1, description=FRIEND_USER_ID_DESCRIPTION),
]


def identity_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in {"authorization", "cookie", "x-request-id", "x-trace-id"}
    }


async def authorize_friend_chat(
    *,
    request: Request,
    bot_id: str,
    caller_id: str,
    owner_id: str,
    friend_user_id: str,
    friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
    stage: str = "draft",
) -> str:
    """Authorize an explicit friend view and prepare ExpertChat's projection."""
    if caller_id not in {friend_user_id, owner_id}:
        raise EngineResourceNotFoundError("friend chat bot not found")
    try:
        allowed = await asyncio.to_thread(
            friendships.is_friend,
            human_id=friend_user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            request_headers=identity_headers(request),
        )
    except FriendshipSourceUnavailableError as error:
        raise EngineDeviceNotReadyError(
            "friendship authorization is temporarily unavailable"
        ) from error
    if not allowed:
        raise EngineResourceNotFoundError("friend chat bot not found")
    try:
        resolved_stage = await asyncio.to_thread(expert.resolve_chat_stage, bot_id, owner_id, stage)
        await asyncio.to_thread(expert.add_chat_bot, friend_user_id, bot_id, owner_id)
        return resolved_stage
    except (BotNotFoundError, ChatPermissionError) as error:
        raise EngineResourceNotFoundError("friend chat bot not found") from error
    except (BotNotActiveError, BotNotPublishedError, ConnectionError) as error:
        raise EngineDeviceNotReadyError("friend bot runtime is not ready") from error


__all__ = ["FriendUserIdQuery", "authorize_friend_chat"]
