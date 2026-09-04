"""Pure mapping and pagination helpers for the public sessions adapter."""
from __future__ import annotations

from typing import Any

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import MessageRole
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import Message, Session
from agentclaw.community.core.engine_runtime.errors import EngineHistoryDepthExceededError
from agentclaw.community.log import get_logger

logger = get_logger()

_LOOKAHEAD = 1
_MAX_HISTORY_DEPTH = 5000

def _openclaw_public_title(session_id: str, title: str) -> str:
    """Remove only the internal suffix OpenClaw adds to a public title."""
    if not session_id or not title:
        return title

    candidates = {session_id}
    parts = session_id.split(":")
    try:
        session_index = parts.index("session")
    except ValueError:
        session_index = -1
    if session_index >= 0 and session_index + 1 < len(parts):
        candidates.add(parts[session_index + 1])
        candidates.add(":".join(parts[session_index:]))

    for suffix in sorted(candidates, key=len, reverse=True):
        marker = f"_{suffix}"
        if suffix and title.endswith(marker):
            return title[: -len(marker)]
    return title


def _map_session(
    data: dict[str, Any], *, engine_type: str | None = None
) -> Session:
    """Engine session dict → public :class:`Session`.

    Source: ``_session_to_dict`` in ``src/engine/.../api/session/router.py``.
    ``user_id`` is dropped (it is the caller) and ``ext_info`` is dropped
    (engine-specific opaque payload with no public contract).
    """
    session_id = str(data.get("id", ""))
    title = str(data.get("title") or "")
    if (engine_type or "").lower() == "openclaw":
        title = _openclaw_public_title(session_id, title)

    return Session(
        session_id=session_id,
        title=title,
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
    engine materialises enough of the provider's list, applies its filters,
    and then slices it (``plugins/openclaw/_session.py``:
    ``raw_sessions[offset : offset+limit]``), so ``offset``/``limit`` mean what
    they say. Message history does not — see
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


def _require_within_depth(page_params: Any) -> None:
    """Refuse a page that would reach past the served history depth.

    The derived total is a lower bound while full pages keep coming and exact
    once a short page arrives — that is the contract ``MessagePage.total``
    publishes, and it is what makes the bound usable. A page clipped by
    :data:`_MAX_HISTORY_DEPTH` breaks it: the window stops at the cap rather
    than at the oldest message, so the page comes back short while more history
    exists, and the caller reads the cap as an exact count. With 50 000
    messages and ``page_size=100``, page 51 returned nothing and reported
    ``total=5001``.

    Serving those pages honestly is not possible here. "Truncated" cannot be
    expressed in a required ``int``, and neither engine route exposes a count to
    put there instead. Refusing is the one answer that is true: the request is
    outside what this endpoint serves, which is a property of the endpoint
    rather than of the data, so it is rejected before the device is touched and
    the same page is rejected whatever the history holds.

    The bound is the window's *end*, not its start. Rejecting only
    ``offset >= depth`` would still admit a page straddling the cap —
    ``page_size=3``, page 1667 starts at 4998 and returns two messages of three,
    short, and therefore exact-looking for the same reason.

    422 matches the surface: ``page_size=101`` is already a 422 from FastAPI's
    own parameter validation, and this is the same class of out-of-range page
    argument.
    """
    offset = (page_params.page - 1) * page_params.page_size
    if offset + page_params.page_size > _MAX_HISTORY_DEPTH:
        raise EngineHistoryDepthExceededError(
            f"page window ends at {offset + page_params.page_size}, past the "
            f"{_MAX_HISTORY_DEPTH}-message depth served"
        )


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
    contract ``MessagePage.total`` documents. That equivalence only holds for
    pages within the depth cap, which is why :func:`_require_within_depth`
    rejects the rest rather than letting a clipped page look like the end.
    """
    size = page_params.page_size
    skip = (page_params.page - 1) * size
    n = len(items)
    end = n - skip
    # The lookahead item exists to prove more history remains; it must never be
    # served as content. _require_within_depth keeps every served page a whole
    # page below the cap, so this floor no longer has a page to trim — it stays
    # as the invariant's backstop, and still binds if a device ever returns more
    # than the limit asked for.
    floor = max(0, n - _MAX_HISTORY_DEPTH)
    visible = items[max(floor, end - size) : end] if end > floor else []
    if reported is not None:
        return reported, visible
    return n, visible
