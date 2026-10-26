"""Session-file list authorization: operator access or an owned friend session."""

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import authorize_friend_chat
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import resolve_operable_bot
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError, EngineResourceNotFoundError
from agentclaw.community.core.expert_chat.errors import BotNotFoundError, ConnectionError


async def authorize_file_list(
    *, request: Request, bot_id: str, session_id: str, user_id: str,
    owner_id: str, stage: RuntimeStage, friend_user_id: str | None,
    relay: EngineRuntimeRelayProtocol, friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
) -> None:
    if friend_user_id is None:
        await resolve_operable_bot(
            relay, bot_id, caller_id=user_id, owner_id=owner_id,
            stage=stage.value, surface="sessions",
        )
        return
    if stage is not RuntimeStage.DRAFT:
        raise EngineResourceNotFoundError("friend chat is available only in draft")
    await authorize_friend_chat(
        request=request, bot_id=bot_id, caller_id=user_id, owner_id=owner_id,
        friend_user_id=friend_user_id, friendships=friendships, expert=expert,
    )
    try:
        # The Backend-owned session index, not a user-id suffix in the key,
        # is authoritative. Never read resource metadata before this check.
        await expert.get_owned_chat_session(
            friend_user_id, bot_id, owner_id, session_id,
            request.cookies.get("IAM_TOKEN") or None,
            bcn_friend_authorized=True, actor_user_id=user_id,
        )
    except BotNotFoundError as error:
        raise EngineResourceNotFoundError("friend session not found") from error
    except ConnectionError as error:
        raise EngineDeviceNotReadyError("friend bot runtime is not ready") from error
