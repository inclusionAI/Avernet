"""Owned-session authorization shared by friend-chat file operations."""

from dataclasses import dataclass

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import authorize_friend_chat
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import resolve_operable_bot
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError, EngineResourceNotFoundError
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError as ManagedBotNotFoundError,
)
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError as ChatSessionNotFoundError,
    ConnectionError,
)
from agentclaw.community.core.runtime_binding.models import RuntimeBindingTarget


@dataclass(frozen=True)
class SessionFileAccess:
    """The trusted runtime facts and subject selected for one file operation."""

    facts: BotFacts
    stage: str
    target: RuntimeBindingTarget


async def authorize_file_list(
    *, request: Request, bot_id: str, session_id: str, user_id: str,
    owner_id: str, stage: RuntimeStage, friend_user_id: str | None,
    relay: EngineRuntimeRelayProtocol, friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
    runtime_target: RuntimeBindingTarget = RuntimeBindingTarget.AUTO,
) -> str:
    if friend_user_id is None:
        await resolve_operable_bot(
            relay, bot_id, caller_id=user_id, owner_id=owner_id,
            stage=stage.value, surface="sessions",
        )
        return stage.value
    # Friend authorization grants access only to the friend's conversation runtime.
    if runtime_target is RuntimeBindingTarget.CALLER_SERVICE:
        raise EngineResourceNotFoundError("service runtime selection is unavailable for friend chat")
    resolved_stage = await authorize_friend_chat(
        request=request, bot_id=bot_id, caller_id=user_id, owner_id=owner_id,
        friend_user_id=friend_user_id, friendships=friendships, expert=expert, stage=stage.value,
    )
    try:
        # The subject's independent Caller container or shared-runtime session
        # index authorizes access before resource metadata is read.
        await expert.get_owned_chat_session(
            friend_user_id, bot_id, owner_id, session_id,
            request.cookies.get("IAM_TOKEN") or None,
            bcn_friend_authorized=True, actor_user_id=user_id,
        )
    except ChatSessionNotFoundError as error:
        raise EngineResourceNotFoundError("friend session not found") from error
    except ConnectionError as error:
        raise EngineDeviceNotReadyError("friend bot runtime is not ready") from error
    return resolved_stage


async def authorize_file_operation(
    *, request: Request, bot_id: str, session_id: str, user_id: str,
    owner_id: str, stage: RuntimeStage, friend_user_id: str | None,
    relay: EngineRuntimeRelayProtocol, friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
    runtime_target: RuntimeBindingTarget = RuntimeBindingTarget.AUTO,
    require_caller_instance: bool = False,
) -> SessionFileAccess:
    """Authorize an operator or the Human owning a non-TeClaw friend session.

    Upload requests do not historically carry ``f_user_id``.  An operator is
    therefore attempted first; only its masked not-found result can fall back
    to friend authorization, which still requires a live BCN friendship and an
    owned session.  The fallback never turns a friend into a Bot collaborator.
    """
    if friend_user_id is None:
        try:
            facts = await resolve_operable_bot(
                relay, bot_id, caller_id=user_id, owner_id=owner_id,
                stage=stage.value, surface="sessions",
            )
            return SessionFileAccess(
                facts=facts, stage=stage.value, target=runtime_target,
            )
        except ManagedBotNotFoundError:
            friend_user_id = user_id

    if require_caller_instance and runtime_target is not RuntimeBindingTarget.CALLER_INSTANCE:
        raise EngineResourceNotFoundError(
            "caller instance selection is required for friend session files"
        )

    resolved_stage = await authorize_friend_chat(
        request=request, bot_id=bot_id, caller_id=user_id, owner_id=owner_id,
        friend_user_id=friend_user_id, friendships=friendships, expert=expert,
        stage=stage.value,
    )
    try:
        await expert.get_owned_chat_session(
            friend_user_id, bot_id, owner_id, session_id,
            request.cookies.get("IAM_TOKEN") or None,
            bcn_friend_authorized=True, actor_user_id=user_id,
        )
        facts = await relay.resolve_bot_off_loop(bot_id, owner_id, owner_id)
    except (ChatSessionNotFoundError, ManagedBotNotFoundError) as error:
        raise EngineResourceNotFoundError("friend session not found") from error
    except ConnectionError as error:
        raise EngineDeviceNotReadyError("friend bot runtime is not ready") from error
    if facts.active_engine.casefold() == "teclaw":
        raise EngineResourceNotFoundError(
            "friend session files are unavailable for TeClaw"
        )
    return SessionFileAccess(
        facts=facts,
        stage=resolved_stage,
        target=RuntimeBindingTarget.AUTO,
    )
