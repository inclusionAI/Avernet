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

FRIEND_USER_ID_DESCRIPTION = (
    "The Human friend whose caller-owned chat resources are addressed. Omit "
    "this parameter to preserve the existing owner/collaborator Engine Runtime "
    "behavior. When supplied, BCN friendship is authoritative; the caller must "
    "be this Human or the Bot owner."
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
    expert: ExpertChatServiceProtocol | None = None,
) -> None:
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
    if expert is not None:
        await asyncio.to_thread(expert.add_chat_bot, friend_user_id, bot_id, owner_id)


__all__ = ["FriendUserIdQuery", "authorize_friend_chat"]
