"""BCN-authorized human-to-Bot chat endpoints.

This is deliberately separate from the device-wide ``sessions`` operator
surface. Every request checks the human/Bot friendship in BCN and every
session operation uses ExpertChat's caller-owned session index. A caller who
is also a Bot collaborator therefore still sees only their own conversations
when using this path.
"""

from __future__ import annotations

import asyncio

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import (
    MessagePage,
    Session,
    SessionCreate,
    SessionFavorite,
    SessionPage,
    SessionUpdate,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas_helpers import (
    _as_list,
    _history_page,
    _history_window,
    _map_message,
    _map_session,
    _page,
    _require_within_depth,
    _window,
)
from agentclaw.community.adapters.http.openapi_v1.human_chat.schemas import (
    HumanChatConnection,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
    page as page_envelope,
)
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
    BotNotFoundError as ExpertBotNotFoundError,
    ConnectionError as ExpertConnectionError,
)
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/human-chat",
    tags=["human-chat"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(refuse_app_only_caller)],
)

SessionIdPath = Annotated[
    str,
    Path(description="Opaque session_id returned by this human-chat API."),
]


async def _resolve_owner_id(
    user_id: UserIdDep,
    owner_id: Annotated[
        str | None,
        Query(
            min_length=1,
            description="Owner of the friend Bot. Defaults to the authenticated user.",
        ),
    ] = None,
) -> str:
    """Resolve an owner without applying the collaborator grant dependency."""
    return owner_id if owner_id is not None else user_id


HumanChatOwnerIdDep = Annotated[str, Depends(_resolve_owner_id)]


def _identity_headers(request: Request) -> dict[str, str]:
    """Forward only identity and trace headers to BCN's trusted boundary."""
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in {"authorization", "cookie", "x-request-id", "x-trace-id"}
    }


async def _authorize(
    *,
    request: Request,
    bot_id: str,
    user_id: str,
    owner_id: str,
    friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
) -> None:
    """Require the current BCN friendship and prepare its legacy projection."""
    try:
        allowed = await asyncio.to_thread(
            friendships.is_friend,
            human_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            request_headers=_identity_headers(request),
        )
    except FriendshipSourceUnavailableError as error:
        raise EngineDeviceNotReadyError(
            "friendship authorization is temporarily unavailable"
        ) from error
    if not allowed:
        # Mask relationship and Bot existence exactly like the existing OpenAPI
        # access gates do for an unauthorized addressed Bot.
        raise EngineResourceNotFoundError("human-chat bot not found")
    await asyncio.to_thread(expert.add_chat_bot, user_id, bot_id, owner_id)


def _iam_token(request: Request) -> str | None:
    return request.cookies.get("IAM_TOKEN") or None


def _raise_expert_error(error: Exception) -> None:
    if isinstance(error, ExpertBotNotFoundError):
        raise EngineResourceNotFoundError("human-chat session not found") from error
    if isinstance(error, ExpertConnectionError):
        raise EngineDeviceNotReadyError("human-chat runtime is not ready") from error
    raise error


async def _ready(
    request: Request,
    bot_id: str,
    user_id: str,
    owner_id: str,
    friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
) -> None:
    await _authorize(
        request=request,
        bot_id=bot_id,
        user_id=user_id,
        owner_id=owner_id,
        friendships=friendships,
        expert=expert,
    )


@router.get("/sessions", response_model=Envelope[SessionPage])
@envelope_errors
async def list_sessions(
    bot_id: BotIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionPage]:
    """List only the authenticated human's sessions with this friend Bot."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    window = _window(page)
    try:
        result = await expert.list_chat_sessions(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            limit=window["limit"],
            offset=window["offset"],
            iam_token=_iam_token(request),
        )
    except Exception as error:
        _raise_expert_error(error)
    mapped = [_map_session(item) for item in _as_list(result)]
    total, items = _page(mapped, page, reported=result.get("total"))
    return page_envelope(total, items, request)


@router.post("/sessions", status_code=201, response_model=Envelope[Session])
@envelope_errors
async def create_session(
    bot_id: BotIdPath,
    body: SessionCreate,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Session]:
    """Create a caller-owned session with this friend Bot."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    try:
        result = await expert.create_chat_session(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            iam_token=_iam_token(request),
        )
        session_id = result.get("session_key")
        if not session_id:
            raise EngineDeviceNotReadyError("human-chat runtime is not ready")
        fields = {
            key: value for key, value in body.model_dump().items() if value is not None
        }
        if fields:
            item = await expert.update_owned_chat_session(
                user_id, bot_id, owner_id, session_id, fields, _iam_token(request)
            )
        else:
            item = await expert.get_owned_chat_session(
                user_id, bot_id, owner_id, session_id, _iam_token(request)
            )
    except Exception as error:
        _raise_expert_error(error)
    return created(_map_session(item), request)


@router.get("/sessions/favorites", response_model=Envelope[SessionPage])
@envelope_errors
async def list_favorites(
    bot_id: BotIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionPage]:
    """List the authenticated human's favorite sessions with this Bot."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    window = _window(page)
    try:
        result = await expert.list_chat_sessions(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            favorite_only=True,
            limit=window["limit"],
            offset=window["offset"],
            iam_token=_iam_token(request),
        )
    except Exception as error:
        _raise_expert_error(error)
    mapped = [_map_session(item) for item in _as_list(result)]
    total, items = _page(mapped, page, reported=result.get("total"))
    return page_envelope(total, items, request)


@router.get("/sessions/{session_id}", response_model=Envelope[Session])
@envelope_errors
async def get_session(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Session]:
    """Get one caller-owned session."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    try:
        item = await expert.get_owned_chat_session(
            user_id, bot_id, owner_id, session_id, _iam_token(request)
        )
    except Exception as error:
        _raise_expert_error(error)
    return envelope(_map_session(item), request)


@router.patch("/sessions/{session_id}", response_model=Envelope[Session])
@envelope_errors
async def update_session(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    body: SessionUpdate,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Session]:
    """Update one caller-owned session."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    fields = {
        key: value for key, value in body.model_dump().items() if value is not None
    }
    try:
        item = await expert.update_owned_chat_session(
            user_id, bot_id, owner_id, session_id, fields, _iam_token(request)
        )
    except Exception as error:
        _raise_expert_error(error)
    return envelope(_map_session(item), request)


@router.delete("/sessions/{session_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_session(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Deleted]:
    """Delete one caller-owned session."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    try:
        await expert.delete_owned_chat_session(user_id, bot_id, owner_id, session_id)
    except Exception as error:
        _raise_expert_error(error)
    return deleted(request)


@router.get(
    "/sessions/{session_id}/connection", response_model=Envelope[HumanChatConnection]
)
@envelope_errors
async def get_connection(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[HumanChatConnection]:
    """Get connection material after verifying this session belongs to the caller."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    try:
        result = await expert.connect_chat_session(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            session_key=session_id,
            iam_token=_iam_token(request),
        )
    except Exception as error:
        _raise_expert_error(error)
    return envelope(
        HumanChatConnection(
            session_id=session_id,
            need_poll=bool(result.get("need_poll")),
            connection=result.get("connection"),
        ),
        request,
    )


async def _set_favorite(
    *,
    request: Request,
    bot_id: str,
    session_id: str,
    user_id: str,
    owner_id: str,
    favorited: bool,
    friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
) -> SessionFavorite:
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    try:
        await expert.set_owned_chat_session_favorite(
            user_id, bot_id, owner_id, session_id, favorited, _iam_token(request)
        )
    except Exception as error:
        _raise_expert_error(error)
    return SessionFavorite(session_id=session_id, favorited=favorited)


@router.put("/sessions/{session_id}/favorite", response_model=Envelope[SessionFavorite])
@envelope_errors
async def add_favorite(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionFavorite]:
    """Idempotently favorite one caller-owned session."""
    result = await _set_favorite(
        request=request,
        bot_id=bot_id,
        session_id=session_id,
        user_id=user_id,
        owner_id=owner_id,
        favorited=True,
        friendships=friendships,
        expert=expert,
    )
    return envelope(result, request)


@router.delete(
    "/sessions/{session_id}/favorite", response_model=Envelope[SessionFavorite]
)
@envelope_errors
async def remove_favorite(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionFavorite]:
    """Idempotently remove one caller-owned favorite marker."""
    result = await _set_favorite(
        request=request,
        bot_id=bot_id,
        session_id=session_id,
        user_id=user_id,
        owner_id=owner_id,
        favorited=False,
        friendships=friendships,
        expert=expert,
    )
    return envelope(result, request)


@router.get("/sessions/{session_id}/messages", response_model=Envelope[MessagePage])
@envelope_errors
async def list_messages(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[MessagePage]:
    """Read one caller-owned session's message history."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    _require_within_depth(page)
    window = _history_window(page)
    try:
        result = await expert.list_owned_chat_session_messages(
            user_id,
            bot_id,
            owner_id,
            session_id,
            limit=window["limit"],
            offset=0,
            iam_token=_iam_token(request),
        )
    except Exception as error:
        _raise_expert_error(error)
    mapped = [_map_message(item, session_id) for item in _as_list(result)]
    total, items = _history_page(mapped, page, reported=result.get("total"))
    return page_envelope(total, items, request)


@router.delete("/sessions/{session_id}/messages", response_model=Envelope[Deleted])
@envelope_errors
async def clear_messages(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: HumanChatOwnerIdDep,
    request: Request,
    friendships: HumanBotFriendshipServiceProtocol = Injected(
        HumanBotFriendshipServiceProtocol
    ),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Deleted]:
    """Clear one caller-owned session's messages while keeping the session."""
    await _ready(request, bot_id, user_id, owner_id, friendships, expert)
    try:
        await expert.clear_owned_chat_session_messages(
            user_id, bot_id, owner_id, session_id, _iam_token(request)
        )
    except Exception as error:
        _raise_expert_error(error)
    return deleted(request)


__all__ = ["router"]
