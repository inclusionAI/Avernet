"""Choose the operator backend or an explicitly authorized subject backend."""

import asyncio

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import authorize_friend_chat
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import resolve_operable_bot
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError as ManagedBotNotFoundError,
)


async def resolve_session_backend(
    *, relay: EngineRuntimeRelayProtocol, friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol, request: Request, bot_id: str, user_id: str,
    owner_id: str, friend_user_id: str | None, stage: RuntimeStage,
    device_uuid: str | None = None,
) -> BotFacts | None:
    if friend_user_id is None:
        return await resolve_operable_bot(
            relay, bot_id, caller_id=user_id, owner_id=owner_id,
            stage=stage.value, surface="sessions",
        )
    # A self subject is not proof of access.  First try the existing owner /
    # collaborator operator gate.  Owner-mode Bots retain the exact legacy
    # shared-runtime path; Caller-mode Bots switch to ExpertChat, whose binding
    # is scoped by this authenticated subject.  Old clients omit f_user_id and
    # therefore never enter this branch.
    if friend_user_id == user_id:
        try:
            facts = await resolve_operable_bot(
                relay, bot_id, caller_id=user_id, owner_id=owner_id,
                stage=stage.value, surface="sessions",
            )
        except ManagedBotNotFoundError:
            # The same self subject may be an ordinary Bot friend.  Preserve the
            # existing BCN authorization path below without turning a failed
            # operator lookup into an authorization bypass.
            pass
        else:
            if facts.call_type.casefold() != "caller":
                return facts
            # ExpertChat's session boundary requires membership in the user's
            # chat list.  The operator gate above is authoritative; this write is
            # an idempotent projection, not an additional permission source.
            await asyncio.to_thread(
                expert.add_chat_bot, user_id, facts.bot_id, facts.owner_id,
            )
            return None
    if device_uuid is not None:
        raise EngineResourceNotFoundError("instance selection is unavailable for friend chat")
    await authorize_friend_chat(
        request=request, bot_id=bot_id, caller_id=user_id, owner_id=owner_id,
        friend_user_id=friend_user_id, friendships=friendships, expert=expert, stage=stage.value,
    )
    return None
