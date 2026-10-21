"""Internal runtime liveness probe for task execution nodes.

The probe asks already-installed OpenAPI/BCS adapters for authoritative runtime
state and, where available, a deterministic heartbeat cursor.  It is an
internal Harness seam: no public task protocol, HTTP contract, or report event
is added here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import TaskNode

logger = logging.getLogger("task.harness.runtime_probe")

RuntimeLivenessState = Literal[
    "active",
    "terminal_success",
    "terminal_failed",
    "unknown",
]


@dataclass(frozen=True)
class RuntimeProbeResult:
    """One liveness observation and the evidence used to derive it.

    ``heartbeat_cursor`` is a stable digest of runtime state/message facts. It
    does not need to be globally meaningful; callers only compare consecutive
    observations to determine whether the runtime showed new activity.
    """

    state: RuntimeLivenessState
    progressed: bool = False
    heartbeat_cursor: str | None = None
    source: str | None = None
    status: str | None = None
    detail: dict[str, Any] | None = None
    error: str | None = None


_TERMINAL_SUCCESS = {"completed", "succeeded", "success", "done", "terminal_success"}
_TERMINAL_FAILED = {
    "failed",
    "error",
    "errored",
    "aborted",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
    "expired",
    "offline",
    "deleted",
    "dormant",
}
_ACTIVE = {
    "active", "running", "processing", "inprogress", "in_progress",
    "pending", "queued", "delivering", "started", "accepted",
}


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _classify_status(value: Any) -> RuntimeLivenessState | None:
    status = _normalized_status(value)
    if not status:
        return None
    if status in _TERMINAL_SUCCESS:
        return "terminal_success"
    if status in _TERMINAL_FAILED:
        return "terminal_failed"
    if status in _ACTIVE:
        return "active"
    return None


def _digest(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - masking must never fail the probe
        text = repr(value)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _message_order_value(value: Any) -> tuple[int, Any, str, int]:
    """Return a comparable, type-safe ordering tuple.

    Runtime APIs do not guarantee message ordering (TeamClaw returns newest
    first in at least one production path). Numeric and RFC3339-like fields are
    compared field by field; their source position is only a tie-breaker for
    otherwise equal ordering fields.
    """
    if isinstance(value, bool):
        return (1, 0, "", 0)
    if isinstance(value, (int, float)):
        num = int(value) if float(value).is_integer() else float(value)
        return (1, num, "", 0)
    if value is None:
        return (0, 0, "", 0)
    text = str(value).strip()
    if not text:
        return (0, 0, "", 0)
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        n = int(number) if number.is_integer() else number
        return (1, n, "", 0)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.timestamp(), text, 0)
    return (0, 0, text, 0)


def _message_sort_key(entry: tuple[int, dict[str, Any]]) -> tuple[Any, ...]:
    index, message = entry
    best = (0, 0, "", 0)
    source = ""
    for key in (
        "seq", "gmt_seq", "version", "updated_at", "gmt_modified",
        "created_at", "gmt_create", "message_id", "id",
    ):
        value = _message_order_value(message.get(key))
        # Prefer the highest independently comparable field. Multiple equal
        # timestamps must still be deterministic, so retain the field name and
        # source position only as later tuple components.
        if value > best:
            best = value
            source = key
        elif value == best and key < source:
            source = key
    # Numeric/temporal fields dominate; lexical identifiers are included with a
    # lower rank so records without timestamps remain stable when possible.
    lexical = str(
        message.get("message_id")
        or message.get("id")
        or ""
    )
    return (*best, source, lexical, index)


def _last_message(messages: Any) -> Any | None:
    if not isinstance(messages, list) or not messages:
        return None
    records = [
        (index, message)
        for index, message in enumerate(messages)
        if isinstance(message, dict)
    ]
    if not records:
        return messages[-1]
    return max(records, key=_message_sort_key)[1]


def _last_message_cursor(messages: Any) -> Any:
    last = _last_message(messages)
    if last is None:
        return None
    if not isinstance(last, dict):
        return {"last": str(last), "count": len(messages)}
    selected = {
        key: last.get(key)
        for key in (
            "id", "message_id", "version", "updated_at", "created_at", "role",
            "content", "text", "seq",
        )
        if last.get(key) is not None
    }
    return {"last": selected or last, "count": len(messages)}


def _join_cursors(*parts: Any) -> str | None:
    values = [part for part in parts if part not in (None, "", {})]
    if not values:
        return None
    return _digest(values)


def _run_cursor(run: dict[str, Any]) -> Any:
    if not isinstance(run, dict):
        return None
    return {
        key: run.get(key)
        for key in ("status", "version", "updated_at", "updated_at_ms", "completed_at")
        if run.get(key) is not None
    }


def _group_cursor(group: dict[str, Any]) -> Any:
    if not isinstance(group, dict):
        return None
    session = group.get("session") if isinstance(group.get("session"), dict) else {}
    return {
        "group": {
            key: group.get(key)
            for key in (
                "status", "version", "updated_at", "updated_at_ms", "state",
            )
            if group.get(key) is not None
        },
        "session": {
            key: session.get(key)
            for key in (
                "status", "version", "updated_at", "updated_at_ms", "state",
            )
            if isinstance(session, dict) and session.get(key) is not None
        },
    }


def _bot_identity_from_node(node: TaskNode) -> str | None:
    """Resolve the bot-addressed identity used by runtime session message reads.

    The Probe owns session-level heartbeats, but ``OpenApiBotAdapter`` must
    address the same runtime handle as the dispatch path. Single-bot
    successors store ``assignee`` and (for newer rows) ``assignee_owner_id``;
    root Relay nodes may already persist the historical composite address.
    ``compose_bot_identity`` preserves that single identity convention
    instead of inventing a second bot-addressing protocol.

    Returns ``None`` only when the node truly has no assignee; callers may
    surface a probe error rather than silently falling back to a default.
    """
    props = getattr(node.run_info, "extend_props", {}) or {}
    assignee = str(getattr(node.run_info, "assignee", "") or "").strip()
    if not assignee:
        return None
    owner_id = str(props.get("assignee_owner_id") or "").strip()
    return compose_bot_identity(assignee, owner_id or None)


class TaskRuntimeLivenessProbe:
    """Read runtime status and heartbeats through existing internal ports."""

    def __init__(self, *, bot: Any = None, bcs: Any = None) -> None:
        self._bot = bot
        self._bcs = bcs

    async def probe(
        self,
        node: TaskNode,
        *,
        previous_cursor: str | None = None,
    ) -> RuntimeProbeResult:
        """Return one internal liveness observation for a dispatched node."""
        props = getattr(node.run_info, "extend_props", {}) or {}
        run_id = str(props.get("run_id") or "").strip() or None
        session_id = str(props.get("session_id") or "").strip() or None
        group_id = str(props.get("group_id") or "").strip() or None
        bot_id = _bot_identity_from_node(node)

        # Single-bot can be turned into a manager-worker group. The authoritative
        # runtime handle decides the probe, not only the persisted run_mode.
        if group_id:
            return await self._probe_group(
                run_id=run_id,
                group_id=group_id,
                session_id=session_id,
                previous_cursor=previous_cursor,
            )
        if run_id:
            return await self._probe_bot_run(
                run_id=run_id,
                session_id=session_id,
                previous_cursor=previous_cursor,
                bot_id=bot_id,
            )
        if session_id:
            return await self._probe_session_messages(
                session_id=session_id,
                previous_cursor=previous_cursor,
                bot_id=bot_id,
            )
        return RuntimeProbeResult(
            state="unknown",
            source="node",
            detail={"reason": "no_dispatch_handle"},
        )

    async def _probe_session_messages(
        self,
        *,
        session_id: str,
        previous_cursor: str | None,
        bot_id: str | None,
    ) -> RuntimeProbeResult:
        """Probe a root execution conversation when only a session id exists.

        The task node remains RUNNING, so message growth is the liveness
        signal. No run handle is inferred and no conversation is created.
        """
        if self._bot is None:
            return RuntimeProbeResult(
                state="unknown",
                source="openapi_bot.session",
                detail={"reason": "port_missing"},
            )
        try:
            messages = await self._bot.get_session_messages(
                session_id, limit=20, bot_id=bot_id
            )
        except Exception as exc:  # noqa: BLE001
            return RuntimeProbeResult(
                state="unknown",
                source="openapi_bot.session",
                error=f"{type(exc).__name__}: {exc}"[:500],
                detail={
                    "session_id": session_id,
                    "bot_id_missing": not bot_id,
                },
            )
        cursor = _join_cursors(
            {"session_id": session_id, "count": len(messages) if isinstance(messages, list) else None},
            _last_message_cursor(messages),
        )
        return self._active_result(
            source="openapi_bot.session",
            status="active",
            cursor=cursor,
            previous_cursor=previous_cursor,
            detail={"session_id": session_id},
        )

    def _active_result(
        self,
        *,
        source: str,
        status: str | None,
        cursor: str | None,
        previous_cursor: str | None,
        detail: dict[str, Any] | None = None,
    ) -> RuntimeProbeResult:
        progressed = bool(previous_cursor and cursor and cursor != previous_cursor)
        return RuntimeProbeResult(
            state="active",
            progressed=progressed,
            heartbeat_cursor=cursor,
            source=source,
            status=status,
            detail=detail,
        )

    async def _probe_bot_run(
        self,
        *,
        run_id: str,
        session_id: str | None,
        previous_cursor: str | None,
        bot_id: str | None,
    ) -> RuntimeProbeResult:
        if self._bot is None:
            return RuntimeProbeResult(
                state="unknown",
                source="openapi_bot",
                detail={"reason": "port_missing"},
            )
        try:
            run = await self._bot.get_run(run_id)
        except Exception as exc:  # noqa: BLE001 - probe must be observable, not fatal
            return RuntimeProbeResult(
                state="unknown",
                source="openapi_bot.run",
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
        if not isinstance(run, dict):
            return RuntimeProbeResult(
                state="unknown",
                source="openapi_bot.run",
                error="run response is not an object",
            )
        status = _normalized_status(run.get("status"))
        state = _classify_status(status) or "unknown"
        if state in {"terminal_success", "terminal_failed"}:
            return RuntimeProbeResult(
                state=state,
                source="openapi_bot.run",
                status=status,
                detail={"run_id": run_id},
            )

        cursor_value = _run_cursor(run)
        message_error: str | None = None
        if session_id:
            try:
                messages = await self._bot.get_session_messages(
                    session_id, limit=20, bot_id=bot_id
                )
                cursor_value = (
                    cursor_value,
                    _last_message_cursor(messages),
                )
            except Exception as exc:  # noqa: BLE001 - heartbeat read is optional
                message_error = f"{type(exc).__name__}: {exc}"[:500]
        cursor = _join_cursors(cursor_value)
        return self._active_result(
            source="openapi_bot.run",
            status=status or "active",
            cursor=cursor,
            previous_cursor=previous_cursor,
            detail={
                "run_id": run_id,
                "session_id": session_id,
                "message_probe_error": message_error,
            },
        )

    async def _probe_group(
        self,
        *,
        run_id: str | None,
        group_id: str,
        session_id: str | None,
        previous_cursor: str | None,
    ) -> RuntimeProbeResult:
        if self._bcs is None:
            return RuntimeProbeResult(
                state="unknown",
                source="bcs",
                detail={"reason": "port_missing"},
            )

        run: dict[str, Any] | None = None
        run_error: str | None = None
        if run_id:
            try:
                value = await self._bcs.get_state_machine_run(run_id)
                if not isinstance(value, dict):
                    raise ValueError("state-machine run response is not an object")
                run = value
            except Exception as exc:  # noqa: BLE001
                run_error = f"{type(exc).__name__}: {exc}"[:500]

        group: dict[str, Any] | None = None
        group_error: str | None = None
        try:
            value = await self._bcs.get_group(group_id)
            if not isinstance(value, dict):
                raise ValueError("group response is not an object")
            group = value
        except Exception as exc:  # noqa: BLE001
            group_error = f"{type(exc).__name__}: {exc}"[:500]

        run_status = _normalized_status((run or {}).get("status"))
        run_state = _classify_status(run_status) if run is not None else None
        if run_state in {"terminal_success", "terminal_failed"}:
            return RuntimeProbeResult(
                state=run_state,
                source="bcs.state_machine_run",
                status=run_status,
                detail={"run_id": run_id, "group_id": group_id},
            )

        session = group.get("session") if isinstance(group, dict) else {}
        session_status = _normalized_status(
            session.get("status") if isinstance(session, dict) else None
        )
        group_status = _normalized_status((group or {}).get("status"))
        group_state = _classify_status(group_status)
        session_state = _classify_status(session_status)
        if group_state == "terminal_failed" or session_state == "terminal_failed":
            return RuntimeProbeResult(
                state="terminal_failed",
                source="bcs.group",
                status=group_status or session_status,
                detail={"group_id": group_id, "run_error": run_error},
            )
        if group_state == "terminal_success" or session_state == "terminal_success":
            return RuntimeProbeResult(
                state="terminal_success",
                source="bcs.group",
                status=group_status or session_status,
                detail={"group_id": group_id, "run_error": run_error},
            )
        if group is None and run is None:
            return RuntimeProbeResult(
                state="unknown",
                source="bcs",
                detail={"group_id": group_id, "run_id": run_id},
                error=group_error or run_error,
            )
        if group is None and run is not None:
            return self._active_result(
                source="bcs.state_machine_run",
                status=run_status,
                cursor=_join_cursors(_run_cursor(run)),
                previous_cursor=previous_cursor,
                detail={"run_error": run_error, "group_id": group_id},
            )

        cursor_value = _group_cursor(group)
        message_error: str | None = None
        if session_id:
            try:
                messages = await self._bcs.get_session_messages(session_id, limit=20)
                cursor_value = (
                    cursor_value,
                    _last_message_cursor(messages),
                )
            except Exception as exc:  # noqa: BLE001 - heartbeat read is optional
                message_error = f"{type(exc).__name__}: {exc}"[:500]
        cursor = _join_cursors(cursor_value)
        return self._active_result(
            source="bcs.group",
            status=group_status or session_status or "active",
            cursor=cursor,
            previous_cursor=previous_cursor,
            detail={
                "group_id": group_id,
                "run_id": run_id,
                "session_id": session_id,
                "run_error": run_error,
                "message_probe_error": message_error,
            },
        )
