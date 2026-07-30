"""Sessions group — ``/openapi/v1/bots/{bot_id}/sessions``.

Wraps the engine's ``/api/sessions`` surface. **Personal bots only** — see
:func:`_require_personal_bot`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

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

#: One extra item is requested beyond the page, purely to learn whether more
#: exist. The session list route takes ``limit``/``offset`` but reports no
#: total, and ``Page.total`` is required — so for that group the total is
#: derived from the window rather than invented.
_LOOKAHEAD = 1


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


def _window(page_params: Any) -> dict[str, int]:
    """The engine query for exactly the requested page, plus one lookahead item.

    Asking the engine for the caller's window — rather than always fetching from
    offset 0 and slicing locally — is what makes pages past the first few
    hundred work at all. Fetching a fixed prefix left every later page empty and
    capped the reported total at the prefix length.
    """
    offset = (page_params.page - 1) * page_params.page_size
    return {"offset": offset, "limit": page_params.page_size + _LOOKAHEAD}


def _page(
    items: list[Any], page_params: Any, *, reported: int | None
) -> tuple[int, list[Any]]:
    """The requested page, plus the best total the engine allows.

    ``reported`` is the engine's own count where it supplies one — the message
    history does, the session list does not. Without it the total is derived
    from the window: exact once the caller reaches the end (a short page proves
    it), and a floor while full pages keep coming. That is the most this API can
    say honestly until the engine reports a total for sessions; inventing a
    larger number would advertise pages that return nothing.
    """
    offset = (page_params.page - 1) * page_params.page_size
    has_more = len(items) > page_params.page_size
    visible = items[: page_params.page_size]
    if reported is not None:
        return reported, visible
    return offset + len(visible) + (1 if has_more else 0), visible


@router.get("", response_model=Envelope[Page[Session]])
@envelope_errors
async def list_sessions(
    bot_id: str,
    page: PageParamsDep,
    principal: PrincipalDep,
    request: Request,
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
) -> Envelope[Page[Session]]:
    """List the bot's sessions."""
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    params: dict[str, Any] = _window(page)
    if agent_id:
        params["agent_id"] = agent_id
    # Both filters are applied upstream, *before* the engine paginates — so
    # they have to travel with the window rather than being applied to what
    # came back, or the page boundaries would not line up with the filter.
    if session_key:
        params["session_key"] = session_key
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET", path="/api/sessions",
        params=params,
    )
    mapped = [_map_session(d) for d in _as_list(result.data)]
    # The session list reports no total; derive it from the window.
    total, items = _page(mapped, page, reported=result.total)
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
        params=_window(page),
    )
    mapped = [_map_message(d, session_id) for d in _as_list(result.data)]
    # The message history *does* report a total, so it is exact — and now that
    # the window follows the caller's page, a total larger than one page is
    # actually reachable rather than advertising pages that return nothing.
    total, items = _page(mapped, page, reported=result.total)
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
