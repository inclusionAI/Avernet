"""Sessions group — ``/openapi/v1/bots/{bot_id}/sessions``.

Wraps the engine's ``/api/sessions`` surface. **Personal bots only** — see
:func:`_require_personal_bot`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import (
    Message,
    Session,
    SessionCreate,
    SessionUpdate,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
    EngineResourceNotFoundError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/sessions", tags=["sessions"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]

#: The only bot type this group serves. A ``service`` bot's device is reached by
#: many callers and the engine's session list is not scoped per caller, so
#: listing there would show the bot's owner other people's conversations.
_SUPPORTED_BOT_TYPE = "personal"

#: Upper bound on what we ask the engine for when building one page.
#:
#: The engine's list route takes ``limit``/``offset`` but never reports a total
#: (``src/engine/.../api/session/router.py``), and ``Page.total`` is required.
#: So we fetch once, slice locally, and report an exact total — the same shape
#: ``openapi_v1/routines/router.py`` uses. The cap stops a bot with a very large
#: history forcing an unbounded fetch; when it bites, ``total`` is a floor and
#: the truncation is logged rather than silently presented as complete.
_MAX_ENGINE_FETCH = 500


def _require_personal_bot(
    relay: EngineRuntimeRelayProtocol, bot_id: str, owner_id: str
) -> None:
    """Resolve the caller's bot and reject non-personal types.

    Runs **before** any device call, deliberately: a filter applied to what the
    device returned would already have fetched every caller's sessions. This
    also performs the owner-scoped resolve, so a foreign ``bot_id`` raises
    ``BotNotFoundError`` here — before a device is touched.
    """
    facts = relay.resolve_bot(bot_id, owner_id)
    if facts.bot_type != _SUPPORTED_BOT_TYPE:
        raise EngineBotTypeNotSupportedError(
            f"sessions are not served for bot_type={facts.bot_type!r}"
        )


def _map_session(data: dict[str, Any]) -> Session:
    """Engine session dict → public :class:`Session`.

    Source: ``_session_to_dict`` in ``src/engine/.../api/session/router.py``.
    ``user_id`` is dropped (it is the caller) and ``ext_info`` is dropped
    (engine-specific opaque payload with no public contract).
    """
    return Session(
        session_id=str(data.get("id", "")),
        title=str(data.get("title") or ""),
        agent_id=str(data.get("agent_id") or ""),
        model=str(data.get("model") or ""),
        permission_mode=str(data.get("permission_mode") or ""),
        cwd=str(data.get("cwd") or ""),
        runtime=str(data.get("runtime") or ""),
        message_count=int(data.get("message_count") or 0),
        gmt_create=str(data.get("gmt_created") or ""),
        gmt_modified=str(data.get("gmt_modified") or ""),
    )


def _map_message(data: dict[str, Any], session_id: str) -> Message:
    """Engine message dict → public :class:`Message`.

    ``metadata`` is dropped: a free-form engine bag with no public contract,
    and therefore a leak risk on a surface whose messages are otherwise fixed.
    An unrecognised ``role`` falls back to ``system`` rather than raising —
    ``MessageRole`` mirrors a Literal in the engine's model, but a stub or a
    newer engine returning something else must not 500 a read.
    """
    raw_role = str(data.get("role") or "")
    try:
        role = Message.model_fields["role"].annotation(raw_role)  # type: ignore[misc]
    except ValueError:
        logger.warning("[engine_runtime] unknown message role %r", raw_role)
        from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
            MessageRole,
        )

        role = MessageRole.SYSTEM
    return Message(
        message_id=str(data.get("id", "")),
        session_id=str(data.get("session_id") or session_id),
        role=role,
        content=str(data.get("content") or ""),
        gmt_create=str(data.get("gmt_created") or ""),
    )


def _as_list(data: Any) -> list[dict[str, Any]]:
    """Engine list payloads are bare lists; tolerate a wrapped ``items`` too."""
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("items") or []
    else:
        raw = []
    return [d for d in raw if isinstance(d, dict)]


def _slice(
    items: list[Any], page_params: Any, *, what: str, reported: int | None = None
) -> tuple[int, list[Any]]:
    """The requested page plus the best total available.

    ``reported`` is the engine's own count where it supplies one. Without it the
    total is the number of items fetched, which the cap can understate — logged
    when that happens rather than presented as complete.
    """
    if reported is None and len(items) >= _MAX_ENGINE_FETCH:
        logger.warning(
            "[engine_runtime] %s hit the %d-item fetch cap; total is a floor",
            what,
            _MAX_ENGINE_FETCH,
        )
    total = reported if reported is not None else len(items)
    start = (page_params.page - 1) * page_params.page_size
    return total, items[start : start + page_params.page_size]


@router.get("", response_model=Envelope[Page[Session]])
@envelope_errors
async def list_sessions(
    bot_id: str,
    page: PageParamsDep,
    principal: PrincipalDep,
    request: Request,
    agent_id: str | None = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Page[Session]]:
    """List the bot's sessions."""
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    params: dict[str, Any] = {"limit": _MAX_ENGINE_FETCH, "offset": 0}
    if agent_id:
        params["agent_id"] = agent_id
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET", path="/api/sessions",
        params=params,
    )
    mapped = [_map_session(d) for d in _as_list(result.data)]
    total, items = _slice(mapped, page, what="sessions")
    return page_envelope(total, items, request)


@router.post("", status_code=201, response_model=Envelope[Session])
@envelope_errors
async def create_session(
    bot_id: str,
    body: SessionCreate,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Session]:
    """Create a session."""
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="POST", path="/api/sessions",
        body={
            "title": body.title,
            "agent_id": body.agent_id,
            "model": body.model,
            # Filled from the principal, never accepted from the caller.
            "user_id": owner_id,
        },
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError("engine returned no session")
    return created(_map_session(result.data), request)


@router.get("/{session_id}", response_model=Envelope[Session])
@envelope_errors
async def get_session(
    bot_id: str,
    session_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Session]:
    """Get one session.

    Pass the `session_id` exactly as the list endpoint returned it. The value
    may contain colons; no encoding is required.
    """
    # A colon is legal in a path segment (RFC 3986), so ids route as-is. An id
    # containing "/" would not be addressable, but no engine id format has one.
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
        path=f"/api/sessions/{session_id}",
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no session {session_id}")
    return envelope(_map_session(result.data), request)


@router.patch("/{session_id}", response_model=Envelope[Session])
@envelope_errors
async def update_session(
    bot_id: str,
    session_id: str,
    body: SessionUpdate,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Session]:
    """Update a session. Omitted fields are left unchanged."""
    # Publicly a PATCH on the resource; the engine models the same operation as
    # a POST to an /update sub-path.
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="POST",
        # QUERY params, not a body. The engine declares this route's fields as
        # bare scalar arguments, which FastAPI binds from the query string —
        # there is no Body(...) on it. Sending a body is silently discarded and
        # the endpoint answers 200 with the unchanged session: a no-op that
        # looks like success.
        path=f"/api/sessions/{session_id}/update", params=payload,
    )
    if not isinstance(result.data, dict):
        raise EngineResourceNotFoundError(f"no session {session_id}")
    return envelope(_map_session(result.data), request)


@router.delete("/{session_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_session(
    bot_id: str,
    session_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Deleted]:
    """Delete a session."""
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="DELETE",
        path=f"/api/sessions/{session_id}",
    )
    return deleted(request)


@router.get("/{session_id}/messages", response_model=Envelope[Page[Message]])
@envelope_errors
async def list_session_messages(
    bot_id: str,
    session_id: str,
    page: PageParamsDep,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Page[Message]]:
    """Read a session's message history."""
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
        path=f"/api/sessions/{session_id}/messages",
        params={"limit": _MAX_ENGINE_FETCH},
    )
    mapped = [_map_message(d, session_id) for d in _as_list(result.data)]
    # Unlike the session list, the message history *does* report a total
    # (``ApiResponse.total``). Prefer it: falling back to len(mapped) would
    # understate a history longer than the fetch cap.
    total, items = _slice(mapped, page, what="messages", reported=result.total)
    return page_envelope(total, items, request)


@router.delete("/{session_id}/messages", response_model=Envelope[Deleted])
@envelope_errors
async def clear_session_messages(
    bot_id: str,
    session_id: str,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[Deleted]:
    """Clear a session's message history, keeping the session."""
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="DELETE",
        path=f"/api/sessions/{session_id}/messages",
    )
    return deleted(request)
