"""Sessions group — ``/openapi/v1/bots/{bot_id}/sessions``.

Wraps the engine's ``/api/sessions`` surface. An **operator console**: served
to the addressed bot's owner and its member-level collaborators, for the
stage the request names (``?stage=``, draft by default), and device-wide —
see ``engine_runtime/gating.py`` and ``core/engine_runtime/gate.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import StreamingResponse

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import (
    Message,
    MessagePage,
    Session,
    SessionCreate,
    SessionFavorite,
    SessionFile,
    SessionFileList,
    SessionFileUploadCompleteRequest,
    SessionFileUploadGrant,
    SessionFileUploadIntentRequest,
    SessionFileUploadIntentResult,
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
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.converter_creation import reconcile_created_session
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.dependencies_session_files import OpenApiSessionFileAdapter
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import resolve_operable_bot
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
    EngineUpstreamError,
)
from agentclaw.community.core.resources.service import FileTooLargeError
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError as ExpertBotNotFoundError,
    ConnectionError as ExpertConnectionError,
)
from agentclaw.community.core.bot_chat.bcn_friendship import (
    FriendshipSourceUnavailableError,
)
from agentclaw.community.core.session_resources.types import SessionResourceRecord
from agentclaw.community.di import Injected
router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/sessions",
    tags=["sessions"],
    route_class=PublicAPIRoute,
)

#: The path parameter naming the session an operation addresses.
SessionIdPath = Annotated[
    str,
    Path(
        description="The session's id, exactly as returned in a session "
        "listing's session_id — use it verbatim, do not re-encode it."
    ),
]


#: One extra item is requested beyond the page, purely to learn whether more
#: exist. Neither engine route reports a total, and ``Page.total`` is required —
#: so for this group the total is derived from the window rather than invented,
#: and both paged routes answer with :class:`BoundedPage` to say so.
_LOOKAHEAD = 1

#: How far back message history is served, in messages. The history fetch is
#: tail-limited and its cost is the whole window, not the page — see
#: :func:`_history_window` — so without a ceiling the page number alone
#: multiplies into an arbitrarily large upstream request. A page reaching past
#: this depth is refused rather than served short; see
#: :func:`_require_within_depth` for why an empty page could not be used.
#: Generous for a conversation; bounded enough that a page number cannot be
#: turned into device load.
_MAX_HISTORY_DEPTH = 5000

def _friend_auth_headers(request: Request) -> dict[str, str]:
    """Forward only identity/trace headers needed by BCN's trusted boundary."""
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in {"authorization", "cookie", "x-request-id", "x-trace-id"}
    }


async def _resolve_session_backend(
    *,
    relay: EngineRuntimeRelayProtocol,
    friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
    request: Request,
    bot_id: str,
    user_id: str,
    owner_id: str,
    stage: RuntimeStage,
):
    """Return Bot facts for the unchanged owner path, or ``None`` for friend.

    A friend is considered only after the existing owner/collaborator gate has
    returned its masked not-found answer. BCN failures fail closed; they never
    fall back to Backend's legacy friend tables.
    """
    try:
        return await resolve_operable_bot(
            relay,
            bot_id,
            caller_id=user_id,
            owner_id=owner_id,
            stage=stage.value,
            surface="sessions",
        )
    except BotNotFoundError as owner_error:
        if stage is not RuntimeStage.DRAFT:
            raise owner_error
        try:
            allowed = await asyncio.to_thread(
                friendships.is_friend,
                human_id=user_id,
                bot_id=bot_id,
                owner_id=owner_id,
                request_headers=_friend_auth_headers(request),
            )
        except FriendshipSourceUnavailableError as error:
            raise EngineDeviceNotReadyError(
                "friendship authorization is temporarily unavailable"
            ) from error
        if not allowed:
            raise owner_error
        # Preserve ExpertChat's existing ownership/runtime implementation. The
        # row is an interaction-list projection, not friendship authority;
        # BCN was checked immediately above on every OpenAPI request.
        await asyncio.to_thread(expert.add_chat_bot, user_id, bot_id, owner_id)
        return None


def _raise_expert_error(error: Exception) -> None:
    if isinstance(error, ExpertBotNotFoundError):
        raise EngineResourceNotFoundError("friend session not found") from error
    if isinstance(error, ExpertConnectionError):
        raise EngineDeviceNotReadyError("friend bot runtime is not ready") from error
    raise error


@router.get("", response_model=Envelope[SessionPage])
@envelope_errors
async def list_sessions(
    bot_id: BotIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    agent_id: Annotated[
        str | None, Query(description="Only sessions belonging to this agent.")
    ] = None,
    session_key: Annotated[
        str | None,
        Query(
            description="Only the session with this key. Pass the value "
            "verbatim; no encoding is required."
        ),
    ] = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionPage]:
    """List the bot's sessions."""
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    params: dict[str, Any] = _window(page)
    if agent_id:
        params["agent_id"] = agent_id
    # Both filters are applied upstream, *before* the engine paginates — so
    # they have to travel with the window rather than being applied to what
    # came back, or the page boundaries would not line up with the filter.
    if session_key:
        params["session_key"] = session_key
    if facts is None:
        try:
            friend_result = await expert.list_chat_sessions(
                user_id=user_id, bot_id=bot_id, owner_id=owner_id,
                session_key=session_key, limit=params["limit"],
                offset=params["offset"],
                iam_token=request.cookies.get("IAM_TOKEN") or None,
            )
        except Exception as error:
            _raise_expert_error(error)
        mapped = [_map_session(d) for d in _as_list(friend_result)]
        total, items = _page(mapped, page, reported=friend_result.get("total"))
        return page_envelope(total, items, request)
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="GET",
        path="/api/sessions",
        params=params,
    )
    mapped = [_map_session(d, engine_type=facts.active_engine) for d in _as_list(result.data)]
    # The session list reports no total; derive it from the window.
    total, items = _page(mapped, page, reported=result.total)
    return page_envelope(total, items, request)


@router.post("", status_code=201, response_model=Envelope[Session])
@envelope_errors
async def create_session(
    bot_id: BotIdPath,
    body: SessionCreate,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Session]:
    """Create a session."""
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    if facts is None:
        try:
            created_result = await expert.create_chat_session(
                user_id=user_id, bot_id=bot_id, owner_id=owner_id,
                iam_token=request.cookies.get("IAM_TOKEN") or None,
            )
            session_key = created_result.get("session_key")
            if not session_key:
                raise EngineDeviceNotReadyError("friend bot runtime is not ready")
            requested = {
                key: value
                for key, value in body.model_dump().items()
                if value is not None
            }
            if requested:
                item = await expert.update_owned_chat_session(
                    user_id, bot_id, owner_id, session_key, requested,
                    request.cookies.get("IAM_TOKEN") or None,
                )
            else:
                item = await expert.get_owned_chat_session(
                    user_id, bot_id, owner_id, session_key,
                    request.cookies.get("IAM_TOKEN") or None,
                )
        except Exception as error:
            _raise_expert_error(error)
        return created(_map_session(item), request)
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="POST",
        path="/api/sessions",
        body={
            "title": body.title,
            "model": body.model,
            **({"cwd": body.cwd} if body.cwd is not None else {}),
            # The verified caller (never accepted from the body), so on a
            # shared bot a session records the operator who created it.
            "user_id": user_id,
        },
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError("engine returned no session")
    item = await reconcile_created_session(relay=relay, facts=facts, bot_id=bot_id, owner_id=owner_id, user_id=user_id, stage=stage, created_item=result.data, requested_title=body.title)
    return created(_map_session(item, engine_type=facts.active_engine), request)


@router.get("/favorites", response_model=Envelope[SessionPage])
@envelope_errors
async def list_session_favorites(
    bot_id: BotIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    agent_id: Annotated[
        str | None, Query(description="Only favorites belonging to this agent.")
    ] = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionPage]:
    """List sessions the acting user has favorited on this bot runtime."""
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    if facts is None:
        window = _window(page)
        try:
            friend_result = await expert.list_chat_sessions(
                user_id=user_id, bot_id=bot_id, owner_id=owner_id,
                favorite_only=True, limit=window["limit"], offset=window["offset"],
                iam_token=request.cookies.get("IAM_TOKEN") or None,
            )
        except Exception as error:
            _raise_expert_error(error)
        mapped = [_map_session(d) for d in _as_list(friend_result)]
        total, items = _page(mapped, page, reported=friend_result.get("total"))
        return page_envelope(total, items, request)
    params: dict[str, Any] = {**_window(page), "user_id": user_id}
    if agent_id:
        params["agent_id"] = agent_id
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="GET",
        path="/api/session-favorites",
        params=params,
    )
    mapped = [_map_session(d, engine_type=facts.active_engine) for d in _as_list(result.data)]
    total, items = _page(mapped, page, reported=result.total)
    return page_envelope(total, items, request)


@router.get("/{session_id}", response_model=Envelope[Session])
@envelope_errors
async def get_session(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Session]:
    """Get one session.

    Pass the `session_id` exactly as the list endpoint returned it. The value
    may contain colons; no encoding is required.
    """
    # A colon is legal in a path segment (RFC 3986), so ids route as-is. An id
    # containing "/" would not be addressable, but no engine id format has one.
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    if facts is None:
        try:
            item = await expert.get_owned_chat_session(
                user_id, bot_id, owner_id, session_id,
                request.cookies.get("IAM_TOKEN") or None,
            )
        except Exception as error:
            _raise_expert_error(error)
        return envelope(_map_session(item), request)
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="GET",
        path=f"/api/sessions/{session_id}",
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no session {session_id}")
    return envelope(_map_session(result.data, engine_type=facts.active_engine), request)


async def _set_session_favorite(
    *,
    bot_id: str,
    session_id: str,
    user_id: str,
    owner_id: str,
    stage: RuntimeStage,
    favorited: bool,
    relay: EngineRuntimeRelayProtocol,
    friendships: HumanBotFriendshipServiceProtocol,
    expert: ExpertChatServiceProtocol,
    request: Request,
) -> SessionFavorite:
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    if facts is None:
        try:
            await expert.set_owned_chat_session_favorite(
                user_id, bot_id, owner_id, session_id, favorited,
                request.cookies.get("IAM_TOKEN") or None,
            )
        except Exception as error:
            _raise_expert_error(error)
        return SessionFavorite(session_id=session_id, favorited=favorited)
    encoded_session_id = quote(session_id, safe="")
    await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="PUT" if favorited else "DELETE",
        path=f"/api/session-favorites/{encoded_session_id}",
        params={"user_id": user_id},
    )
    return SessionFavorite(session_id=session_id, favorited=favorited)


@router.put("/{session_id}/favorite", response_model=Envelope[SessionFavorite])
@envelope_errors
async def add_session_favorite(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionFavorite]:
    """Idempotently favorite one session for the acting user."""
    result = await _set_session_favorite(
        bot_id=bot_id,
        session_id=session_id,
        user_id=user_id,
        owner_id=owner_id,
        stage=stage,
        favorited=True,
        relay=relay,
        friendships=friendships,
        expert=expert,
        request=request,
    )
    return envelope(result, request)


@router.delete("/{session_id}/favorite", response_model=Envelope[SessionFavorite])
@envelope_errors
async def remove_session_favorite(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[SessionFavorite]:
    """Idempotently remove the acting user's favorite marker."""
    result = await _set_session_favorite(
        bot_id=bot_id,
        session_id=session_id,
        user_id=user_id,
        owner_id=owner_id,
        stage=stage,
        favorited=False,
        relay=relay,
        friendships=friendships,
        expert=expert,
        request=request,
    )
    return envelope(result, request)


@router.patch("/{session_id}", response_model=Envelope[Session])
@envelope_errors
async def update_session(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    body: SessionUpdate,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Session]:
    """Update a session. Omitted fields are left unchanged."""
    # Publicly a PATCH on the resource; the engine models the same operation as
    # a POST to an /update sub-path.
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if facts is None:
        try:
            item = await expert.update_owned_chat_session(
                user_id, bot_id, owner_id, session_id, payload,
                request.cookies.get("IAM_TOKEN") or None,
            )
        except Exception as error:
            _raise_expert_error(error)
        return envelope(_map_session(item), request)
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="POST",
        # QUERY params, not a body. The engine declares this route's fields as
        # bare scalar arguments, which FastAPI binds from the query string —
        # there is no Body(...) on it. Sending a body is silently discarded and
        # the endpoint answers 200 with the unchanged session: a no-op that
        # looks like success.
        path=f"/api/sessions/{session_id}/update",
        params=payload,
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no session {session_id}")
    return envelope(_map_session(result.data, engine_type=facts.active_engine), request)


@router.delete("/{session_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_session(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Deleted]:
    """Delete a session."""
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    if facts is None:
        try:
            await expert.delete_owned_chat_session(
                user_id, bot_id, owner_id, session_id
            )
        except Exception as error:
            _raise_expert_error(error)
        return deleted(request)
    await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="DELETE",
        path=f"/api/sessions/{session_id}",
    )
    return deleted(request)


SessionFileResourceId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        description="Opaque Session File resource id returned by upload-intents.",
    ),
]

DispositionQuery = Annotated[
    str,
    Query(
        pattern="^(inline|attachment)$",
        description="Render the file inline or download it as an attachment.",
    ),
]


def _session_file_resource(record: SessionResourceRecord) -> SessionFile:
    return SessionFile(
        resource_id=record.resource_id,
        display_name=record.display_name,
        status=record.status.value,
        size_bytes=record.size_bytes,
        content_hash=record.client_content_hash,
        task_version=record.task_version,
        error_code=_session_file_public_error(record.error_code),
    )


def _session_file_public_error(value: str | None) -> str | None:
    if value is None:
        return None
    if value in {"dispatch_failed", "engine_unavailable"}:
        return value
    return "materialization_failed"


def _session_file_not_found(exc: ValueError) -> EngineResourceNotFoundError:
    raise EngineResourceNotFoundError("session file is unavailable") from exc


def _session_file_headers(headers: object) -> dict[str, str]:
    if not hasattr(headers, "items"):
        return {"Content-Type": "application/octet-stream"}
    # COSEC: the upstream header bag is untrusted at this public boundary;
    # forward only a fixed response-header allowlist after rejecting CR/LF.
    allowed = {"content-type", "content-length", "content-disposition", "retry-after", "cache-control"}
    safe: dict[str, str] = {}
    for key, value in headers.items():
        normalized = str(key).lower()
        if normalized not in allowed or not isinstance(value, str):
            continue
        if "\r" in value or "\n" in value:
            continue
        if normalized == "content-length" and not value.isdecimal():
            continue
        if normalized == "retry-after" and not value.isdecimal():
            continue
        if normalized == "cache-control" and value.lower() != "no-store":
            continue
        safe["-".join(part.capitalize() for part in normalized.split("-"))] = value
    safe.setdefault("Content-Type", "application/octet-stream")
    return safe


@router.post(
    "/{session_id}/files/upload-intents",
    status_code=201,
    response_model=Envelope[SessionFileUploadIntentResult],
)
@envelope_errors
async def create_session_file_upload_intents(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    body: SessionFileUploadIntentRequest,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    adapter: OpenApiSessionFileAdapter = Injected(OpenApiSessionFileAdapter),
) -> Envelope[SessionFileUploadIntentResult]:
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="sessions",
    )
    try:
        intents = adapter.create_upload_intents(
            actor_user_id=user_id,
            owner_id=owner_id,
            bot_id=bot_id,
            session_key=session_id,
            stage=stage.value,
            engine_type=facts.active_engine,
            files=[
                (item.filename, item.size_bytes, item.content_hash)
                for item in body.files
            ],
        )
    except ValueError as exc:
        _session_file_not_found(exc)
    files = [
        SessionFileUploadGrant(
            **_session_file_resource(intent.resource).model_dump(),
            upload_url=intent.grant.upload_url,
            transfer_id=intent.grant.transfer_id,
            upload_type=intent.grant.upload_type,
            http_method=intent.grant.http_method,
            expires_at=intent.grant.expires_at,
            upload_session_id=intent.grant.upload_session_id,
            part_size=intent.grant.part_size,
            part_count=intent.grant.part_count,
            parts=intent.grant.parts,
        )
        for intent in intents
    ]
    return created(SessionFileUploadIntentResult(files=files), request)


@router.post(
    "/{session_id}/files/upload-complete", response_model=Envelope[SessionFile]
)
@envelope_errors
async def complete_session_file_upload(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    body: SessionFileUploadCompleteRequest,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    adapter: OpenApiSessionFileAdapter = Injected(OpenApiSessionFileAdapter),
) -> Envelope[SessionFile]:
    await resolve_operable_bot(
        relay, bot_id, caller_id=user_id, owner_id=owner_id, stage=stage.value,
        surface="sessions",
    )
    try:
        record = adapter.complete_upload(
            owner_id=owner_id,
            bot_id=bot_id,
            session_key=session_id,
            resource_id=body.resource_id,
            transfer_id=body.transfer_id,
        )
    except ValueError as exc:
        _session_file_not_found(exc)
    return envelope(_session_file_resource(record), request)


@router.get(
    "/{session_id}/files/{resource_id}/materialize-status",
    response_model=Envelope[SessionFile],
)
@envelope_errors
async def session_file_materialize_status(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    resource_id: SessionFileResourceId,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    adapter: OpenApiSessionFileAdapter = Injected(OpenApiSessionFileAdapter),
) -> Envelope[SessionFile]:
    await resolve_operable_bot(
        relay, bot_id, caller_id=user_id, owner_id=owner_id, stage=stage.value,
        surface="sessions",
    )
    try:
        record = adapter.get_status(
            owner_id=owner_id,
            bot_id=bot_id,
            session_key=session_id,
            resource_id=resource_id,
        )
    except ValueError as exc:
        _session_file_not_found(exc)
    return envelope(_session_file_resource(record), request)


@router.get("/{session_id}/files", response_model=Envelope[SessionFileList])
@envelope_errors
async def list_ready_session_files(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    adapter: OpenApiSessionFileAdapter = Injected(OpenApiSessionFileAdapter),
) -> Envelope[SessionFileList]:
    await resolve_operable_bot(
        relay, bot_id, caller_id=user_id, owner_id=owner_id, stage=stage.value,
        surface="sessions",
    )
    try:
        records = adapter.list_ready(
            owner_id=owner_id,
            bot_id=bot_id,
            session_key=session_id,
        )
    except ValueError as exc:
        _session_file_not_found(exc)
    return envelope(
        SessionFileList(files=[_session_file_resource(record) for record in records]),
        request,
    )


@router.get(
    "/{session_id}/files/{resource_id}/content",
    response_model=None,
    responses={
        200: {"description": "File content or a ready external-download descriptor."},
        202: {"description": "Large attachment download is being prepared."},
        413: {"description": "Inline preview is too large."},
    },
)
@envelope_errors
async def stream_session_file_content(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    resource_id: SessionFileResourceId,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    disposition: DispositionQuery = "inline",
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    adapter: OpenApiSessionFileAdapter = Injected(OpenApiSessionFileAdapter),
) -> StreamingResponse:
    await resolve_operable_bot(
        relay, bot_id, caller_id=user_id, owner_id=owner_id, stage=stage.value,
        surface="sessions",
    )
    try:
        record, upstream = await adapter.open_content(
            owner_id=owner_id,
            bot_id=bot_id,
            session_key=session_id,
            resource_id=resource_id,
            disposition=disposition,
        )
    except ValueError as exc:
        if str(exc) == "resource_preview_too_large":
            raise FileTooLargeError("File too large for preview") from exc
        if str(exc) == "engine_content_unavailable":
            raise EngineUpstreamError("session file content unavailable") from exc
        _session_file_not_found(exc)
    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.body:
                if isinstance(chunk, bytes):
                    yield chunk
        finally:
            await upstream.close()
    headers = _session_file_headers(upstream.headers)
    if upstream.status_code == 200 and headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
        headers.setdefault("Content-Disposition", f'{disposition}; filename="{record.filename}"')
    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=headers,
    )


@router.delete("/{session_id}/files/{resource_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_session_file(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    resource_id: SessionFileResourceId,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    adapter: OpenApiSessionFileAdapter = Injected(OpenApiSessionFileAdapter),
) -> Envelope[Deleted]:
    await resolve_operable_bot(
        relay, bot_id, caller_id=user_id, owner_id=owner_id, stage=stage.value,
        surface="sessions",
    )
    try:
        adapter.delete(
            owner_id=owner_id,
            bot_id=bot_id,
            session_key=session_id,
            resource_id=resource_id,
        )
    except ValueError as exc:
        _session_file_not_found(exc)
    return deleted(request)


@router.get("/{session_id}/messages", response_model=Envelope[MessagePage])
@envelope_errors
async def list_session_messages(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[MessagePage]:
    """Read a session's message history, newest page first.

    Page 1 is the most recent messages; paging forward walks back through the
    history. Messages are chronological within a page.

    History is served to a depth of 5000 messages. A page reaching past that
    depth is rejected with 422 rather than returned empty, so an end of history
    is never confused with the limit of what this endpoint serves.
    """
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    _require_within_depth(page)
    if facts is None:
        window = _history_window(page)
        try:
            friend_result = await expert.list_owned_chat_session_messages(
                user_id, bot_id, owner_id, session_id,
                limit=window["limit"], offset=0,
                iam_token=request.cookies.get("IAM_TOKEN") or None,
            )
        except Exception as error:
            _raise_expert_error(error)
        mapped = [_map_message(d, session_id) for d in _as_list(friend_result)]
        total, items = _history_page(
            mapped, page, reported=friend_result.get("total")
        )
        return page_envelope(total, items, request)
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="GET",
        path=f"/api/sessions/{session_id}/messages",
        # The history route tail-limits rather than paginating, so the offset is
        # applied here instead of being sent. See ``_history_window``.
        params=_history_window(page),
    )
    mapped = [_map_message(d, session_id) for d in _as_list(result.data)]
    # The engine's envelope carries a total field, so it is used when filled —
    # but both bundled adapters return None for history, so this is normally the
    # derived value. See ``_history_page``.
    total, items = _history_page(mapped, page, reported=result.total)
    return page_envelope(total, items, request)


@router.delete("/{session_id}/messages", response_model=Envelope[Deleted])
@envelope_errors
async def clear_session_messages(
    bot_id: BotIdPath,
    session_id: SessionIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
) -> Envelope[Deleted]:
    """Clear a session's message history, keeping the session."""
    facts = await _resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, stage=stage,
    )
    if facts is None:
        try:
            await expert.clear_owned_chat_session_messages(
                user_id, bot_id, owner_id, session_id,
                request.cookies.get("IAM_TOKEN") or None,
            )
        except Exception as error:
            _raise_expert_error(error)
        return deleted(request)
    await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        method="DELETE",
        path=f"/api/sessions/{session_id}/messages",
    )
    return deleted(request)
