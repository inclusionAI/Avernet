"""Connection endpoint — ``GET /openapi/v1/bots/{bot_id}/connection``.

The public replacement for the device-connection hand-off. Returns finished
socket URLs; the caller opens the socket itself. Chat is not relayed through
this API, so the engine's frame format never becomes a public contract.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.connection.schemas import (
    Connection,
    FriendConnection,
    Socket,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import (
    FriendUserIdQuery,
    authorize_friend_chat,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
)
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError as ExpertBotNotFoundError,
    ConnectionError as ExpertConnectionError,
)
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/connection",
    tags=["connection"],
    route_class=PublicAPIRoute,
)


@router.get("", response_model=Envelope[Connection | FriendConnection])
@envelope_errors
async def get_connection(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    f_user_id: FriendUserIdQuery = None,
    session_id: Annotated[
        str | None,
        Query(description="Required with f_user_id; use the session_id verbatim."),
    ] = None,
    connections: EngineConnectionServiceProtocol = Injected(
        EngineConnectionServiceProtocol
    ),
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Connection | FriendConnection]:
    """Get usable socket connections for a bot."""
    if f_user_id is not None:
        if stage is not RuntimeStage.DRAFT or not session_id:
            raise EngineResourceNotFoundError(
                "friend connection requires a draft session_id"
            )
        await authorize_friend_chat(
            request=request,
            bot_id=bot_id,
            caller_id=user_id,
            owner_id=owner_id,
            friend_user_id=f_user_id,
            friendships=friendships,
            expert=expert,
        )
        try:
            result = await expert.connect_chat_session(
                user_id=f_user_id,
                bot_id=bot_id,
                owner_id=owner_id,
                session_key=session_id,
                iam_token=request.cookies.get("IAM_TOKEN") or None,
            )
        except ExpertBotNotFoundError as error:
            raise EngineResourceNotFoundError("friend session not found") from error
        except ExpertConnectionError as error:
            raise EngineDeviceNotReadyError("friend runtime is not ready") from error
        return envelope(
            FriendConnection(
                session_id=session_id,
                need_poll=bool(result.get("need_poll")),
                connection=result.get("connection"),
            ),
            request,
        )
    # No capability probe: the only socket offered is chat, derived from the
    # bot's active engine, which is a backend fact. The terminal socket that
    # once needed one was removed — the spec excludes an interactive shell from
    # v1 at any scope. That also removes a device call from this endpoint.
    # In a worker thread: ``build`` is synchronous and talks to the device
    # provider (device resolution, then ``get_device_connection``), which on the
    # BaaS path is a blocking ``httpx`` call with a 30-second timeout. Inline,
    # one slow provider lookup parks the event loop and stalls every unrelated
    # request on this worker. Offloading here rather than making ``build``
    # ``async`` keeps it callable from the sync paths and keeps its declared
    # signature — which ``test_service_api_conformance`` pins, coroutine status
    # included — the same on both sides.
    result = await asyncio.to_thread(
        connections.build,
        bot_id=bot_id,
        owner_id=owner_id,
        caller_id=user_id,
        stage=stage.value,
    )
    return envelope(
        Connection(
            engine=result.engine,
            expires_at=result.expires_at,
            sockets=[Socket(kind=s.kind, url=s.url) for s in result.sockets],
        ),
        request,
    )
