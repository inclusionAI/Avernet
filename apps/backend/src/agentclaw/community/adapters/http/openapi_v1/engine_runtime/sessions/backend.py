"""Choose the operator backend or the explicitly authorized friend backend."""

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import authorize_friend_chat
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import resolve_operable_bot
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.core.engine_runtime.models import BotFacts


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
    if device_uuid is not None:
        raise EngineResourceNotFoundError("instance selection is unavailable for friend chat")
    await authorize_friend_chat(
        request=request, bot_id=bot_id, caller_id=user_id, owner_id=owner_id,
        friend_user_id=friend_user_id, friendships=friendships, expert=expert, stage=stage.value,
    )
    return None
