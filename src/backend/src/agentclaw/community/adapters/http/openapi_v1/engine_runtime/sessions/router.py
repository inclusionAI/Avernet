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
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import (
    Message,
    MessagePage,
    Session,
    SessionCreate,
    SessionPage,
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
#: exist. Neither engine route reports a total, and ``Page.total`` is required —
#: so for this group the total is derived from the window rather than invented,
#: and both paged routes answer with :class:`BoundedPage` to say so.
_LOOKAHEAD = 1

#: How far back message history is served, in messages. The history fetch is
#: tail-limited and its cost is the whole window, not the page — see
#: :func:`_history_window` — so without a ceiling the page number alone
#: multiplies into an arbitrarily large upstream request. Past this depth the
#: endpoint returns an empty page, which is the documented end-of-history
#: signal. Generous for a conversation; bounded enough that a page number
#: cannot be turned into device load.
_MAX_HISTORY_DEPTH = 5000


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

    This is the **session list**'s window, and it is the straightforward one: the
    engine paginates a fully-materialised list
    (``plugins/openclaw/_session.py``: ``raw_sessions[offset : offset+limit]``),
    so ``offset``/``limit`` mean what they say. Message history does not — see
    :func:`_history_window`.
    """
    return {
        "offset": (page_params.page - 1) * page_params.page_size,
        "limit": page_params.page_size + _LOOKAHEAD,
    }


def _history_window(page_params: Any) -> dict[str, int]:
    """The engine query for a page of message history.

    The history route does not paginate — it **tail-limits**. ``limit`` selects
    the *newest* N messages, both in the bundled providers
    (``local/openclaw/plugin_impl.py``, ``local/claude_code/plugin_impl.py``:
    ``items[-limit:]``) and in the ``chat.history`` RPC they mirror, whose only
    windowing parameter is ``limit``. The adapter then applies ``offset`` *to
    that tail* (``messages[offset : offset+limit]``).

    Those two compose badly. Growing ``limit`` to cover the offset moves the
    tail's start back by exactly the offset, and skipping the offset walks
    forward to the same place: with 100 messages and ``page_size=20``, page 1
    and page 2 both return messages 79–98. Sending a page-sized limit instead
    just makes every page past the first empty.

    So the offset is not sent at all. We ask for the newest
    ``offset + page_size + 1`` messages and cut the page out of that tail
    ourselves in :func:`_history_page`, which is the one shape the engine's
    "newest N" contract can serve exactly.

    That request grows with the page number, and ``page`` has no upper bound —
    ``page_size`` is capped at 100 but the page index is only ``ge=1``. Since
    both bundled adapters forward ``limit`` upstream *before* slicing, an
    unclamped window would let ``page=1000000`` ask a tenant's device for a
    hundred million messages to answer with at most a hundred. The window is
    therefore clamped to :data:`_MAX_HISTORY_DEPTH`, which is also the depth
    the endpoint documents itself as serving.
    """
    offset = (page_params.page - 1) * page_params.page_size
    want = offset + page_params.page_size + _LOOKAHEAD
    return {"offset": 0, "limit": min(want, _MAX_HISTORY_DEPTH + _LOOKAHEAD)}


def _page(
    items: list[Any], page_params: Any, *, reported: int | None
) -> tuple[int, list[Any]]:
    """The requested page, plus the best total the engine allows.

    ``reported`` is the engine's own count where it supplies one. Both bundled
    engines return ``total=None`` for message history and the session list has
    no total field at all, so in practice this is the derived branch — but a
    corp engine that fills it is preferred over anything computed here.

    Derived: exact once the caller reaches the end (a short page proves it), and
    a lower bound while full pages keep coming. Neither engine route exposes a
    count, and the only way to compute one would be to fetch every record —
    which for sessions fans out a ``chat.history`` RPC per session. A bound that
    is honest about being a bound beats advertising pages that return nothing;
    the endpoints document it on the field.
    """
    offset = (page_params.page - 1) * page_params.page_size
    has_more = len(items) > page_params.page_size
    visible = items[: page_params.page_size]
    if reported is not None:
        return reported, visible
    return offset + len(visible) + (1 if has_more else 0), visible


def _history_page(
    items: list[Any], page_params: Any, *, reported: int | None
) -> tuple[int, list[Any]]:
    """The requested page cut out of a "newest N" tail. See :func:`_history_window`.

    ``items`` is the newest ``offset + page_size + 1`` messages in chronological
    order, so the page is measured from the *end*: page 1 is the most recent
    ``page_size`` messages, page 2 the ``page_size`` before those. Paging a chat
    history backwards is the only direction a tail-limited fetch can serve —
    reaching the oldest page directly would mean fetching the whole history,
    which has no count to size the request from.

    Messages stay in chronological order *within* a page; it is the pages that
    run newest-first.

    The total falls out of the same window and is stronger than the session
    list's: when the tail comes back short, it is the whole history, so the
    count is exact. While it comes back full, it is a lower bound — the same
    contract ``MessagePage.total`` documents.
    """
    size = page_params.page_size
    skip = (page_params.page - 1) * size
    n = len(items)
    end = n - skip
    visible = items[max(0, end - size) : end] if end > 0 else []
    if reported is not None:
        return reported, visible
    return n, visible


@router.get("", response_model=Envelope[SessionPage])
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
) -> Envelope[SessionPage]:
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


@router.get("/{session_id}/messages", response_model=Envelope[MessagePage])
@envelope_errors
async def list_session_messages(
    bot_id: str,
    session_id: str,
    page: PageParamsDep,
    principal: PrincipalDep,
    request: Request,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[MessagePage]:
    """Read a session's message history, newest page first.

    Page 1 is the most recent messages; paging forward walks back through the
    history. Messages are chronological within a page.

    History is served to a depth of 5000 messages. Pages past that depth come
    back empty, the same signal as reaching the end of a shorter history.
    """
    owner_id = caller_owner_id(principal)
    _require_personal_bot(relay, bot_id, owner_id)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, method="GET",
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
