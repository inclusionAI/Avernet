"""Connection endpoint — ``GET /openapi/v1/bots/{bot_id}/connection``.

The public replacement for the device-connection hand-off. Returns finished
socket URLs; the caller opens the socket itself. Chat is not relayed through
this API, so the engine's frame format never becomes a public contract.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import datetime, timezone
import json
from typing import Annotated
from urllib.parse import quote, urlsplit

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
    EngineUpstreamError,
)
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError as ExpertBotNotFoundError,
    ConnectionError as ExpertConnectionError,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.config import GatewayEndpoint
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/connection",
    tags=["connection"],
    route_class=PublicAPIRoute,
)

_ENGINE_WS_PREFIX = "/openapi/v1/bots/messages/ws"


def _friend_token(connection: dict[str, object]) -> str:
    headers = connection.get("headers")
    header_token = (
        headers.get("x-proxypass-token") if isinstance(headers, dict) else None
    )
    token = str(header_token or connection.get("token") or "")
    if not token:
        raise EngineUpstreamError("friend connection carries no proxy credential")
    return token


def _token_expiry(token: str) -> str:
    """Read the issuer-stamped JWT expiry without treating it as authorization."""
    try:
        encoded_payload = token.split(".", 2)[1]
        padding = "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload + padding))
        expires_at = int(payload["exp"])
    except (
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        OSError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise EngineUpstreamError(
            "friend connection credential carries no usable expiry"
        ) from error
    return datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()


def _gateway_ws_origin(gateway: GatewayEndpoint) -> str:
    base = gateway.base_url.strip().rstrip("/")
    try:
        parts = urlsplit(base)
    except ValueError as error:
        raise EngineUpstreamError("gateway endpoint is invalid") from error
    schemes = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
    ws_scheme = schemes.get(parts.scheme.lower())
    if not ws_scheme or not parts.netloc or parts.path or parts.query or parts.fragment:
        raise EngineUpstreamError("gateway endpoint is not a bare origin")
    return f"{ws_scheme}://{parts.netloc}"


def _ready_friend_connection(
    raw: dict[str, object], gateway: GatewayEndpoint
) -> Connection:
    """Publish ExpertChat connection material through the OpenAPI gateway."""
    engine = str(raw.get("engine_type") or "")
    target = str(raw.get("target") or "")
    if not engine or not target:
        raise EngineUpstreamError(
            "friend connection carries no engine or routing target"
        )
    token = _friend_token(raw)
    engine_path = f"/api/{quote(engine, safe='')}/ws"
    url = (
        f"{_gateway_ws_origin(gateway)}{_ENGINE_WS_PREFIX}/"
        f"{quote(target, safe='@:[]')}{engine_path}"
        f"?x-proxypass-token={quote(token, safe='')}"
    )
    return Connection(
        engine=engine,
        expires_at=_token_expiry(token),
        sockets=[Socket(kind="chat", url=url)],
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
    gateway: GatewayEndpoint = Injected(GatewayEndpoint),
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
                bcn_friend_authorized=True,
            )
        except ExpertBotNotFoundError as error:
            raise EngineResourceNotFoundError("friend session not found") from error
        except ExpertConnectionError as error:
            raise EngineDeviceNotReadyError("friend runtime is not ready") from error
        need_poll = bool(result.get("need_poll"))
        raw_connection = result.get("connection")
        if need_poll:
            return envelope(
                FriendConnection(
                    session_id=session_id,
                    need_poll=need_poll,
                    connection=None,
                ),
                request,
            )
        if not isinstance(raw_connection, dict):
            raise EngineUpstreamError(
                "ready friend connection carries no connection material"
            )
        return envelope(_ready_friend_connection(raw_connection, gateway), request)
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
