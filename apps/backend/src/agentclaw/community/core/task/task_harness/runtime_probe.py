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
    "probe_unavailable",
    "activity_unknown",
]


@dataclass(frozen=True)
class RuntimeProbeResult:
    """One liveness observation and the evidence used to derive it.

    ``heartbeat_cursor`` carries an activity run identity and sequence for Bot
    sessions, or a semantic digest for legacy run/group probes. New generations
    reset the observation baseline instead of counting as execution progress.
    """

    state: RuntimeLivenessState
    progressed: bool = False
    baseline_reset: bool = False
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
        "timestamp", "created_at", "gmt_create", "message_id", "id",
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
    # Volatile timestamps and a new empty assistant placeholder are not business
    # progress. Only message payload makes the event semantically observable.
    payload = {
        key: last.get(key)
        for key in (
            "content", "text", "tool_call", "tool_calls", "tool_result", "result",
        )
        if last.get(key) not in (None, "", [], {}, "[]", "{}")
    }
    if not payload:
        return None
    identifiers = {
        key: last.get(key)
        for key in ("id", "message_id", "seq")
        if last.get(key) is not None
    }
    return {"last": {**identifiers, **payload}, "count": len(messages)}


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
        for key in ("status", "completed_at")
        if run.get(key) is not None
    }


def _group_cursor(group: dict[str, Any]) -> Any:
    if not isinstance(group, dict):
        return None
    session = group.get("session") if isinstance(group.get("session"), dict) else {}
    return {
        "group": {
            key: group.get(key)
            for key in ("status", "state")
            if group.get(key) is not None
        },
        "session": {
            key: session.get(key)
            for key in (
                "status", "state",
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

    @staticmethod
    def _unavailable(
        *,
        source: str,
        error: Exception,
        detail: dict[str, Any],
    ) -> RuntimeProbeResult:
        """Build a ``probe_unavailable`` result.

        An unreadable runtime is not evidence of a dead Bot. The harness must not
        consume same-session recovery attempts from this state, otherwise a
        transient predeploy outage on a single-server pre environment can be
        mistaken for a Bot runtime hang.
        """
        return RuntimeProbeResult(
            state="probe_unavailable",
            source=source,
            error=f"{type(error).__name__}: {error}"[:500],
            detail={"reason": "probe_request_failed", **detail},
        )

    def __init__(
        self, *, bot: Any = None, bcs: Any = None, bot_tokens: Any = None
    ) -> None:
        self._bot = bot
        self._bcs = bcs
        self._bot_tokens = bot_tokens

    async def probe(
        self,
        node: TaskNode,
        *,
        previous_cursor: str | None = None,
    ) -> RuntimeProbeResult:
        """Return one internal liveness observation for a dispatched node."""
        props = getattr(node.run_info, "extend_props", {}) or {}
        # Prefer the latest same-session resume run over the original dispatch
        # run. Otherwise the probe keeps reading the first terminal run while
        # the resumed run is still active.
        raw_run_id = (
            props.get("relay_harness_resume_run_id") or props.get("run_id")
        )
        run_id = str(raw_run_id or "").strip() or None
        session_id = str(props.get("session_id") or "").strip() or None
        group_id = str(props.get("group_id") or "").strip() or None
        lifecycle_stage = (
            str(props.get("lifecycle_stage") or "").strip() or None
        )
        bot_id = _bot_identity_from_node(node)

        # Single-bot can be turned into a manager-worker group. The authoritative
        # runtime handle decides the probe, not only the persisted run_mode.
        if group_id:
            return await self._probe_group(
                node=node,
                run_id=None if props.get("relay_harness_resume_message_id") else run_id,
                group_id=group_id,
                session_id=session_id,
                previous_cursor=previous_cursor,
            )
        if session_id:
            return await self._probe_bot_runtime(
                session_id=session_id, previous_cursor=previous_cursor,
                bot_id=bot_id, lifecycle_stage=lifecycle_stage,
            )
        if run_id:
            return await self._probe_bot_run(
                run_id=run_id,
                session_id=session_id,
                previous_cursor=previous_cursor,
                bot_id=bot_id,
                lifecycle_stage=lifecycle_stage,
            )
        return RuntimeProbeResult(
            state="unknown",
            source="node",
            detail={"reason": "no_dispatch_handle"},
        )

    async def _probe_bot_runtime(
        self, *, session_id: str, previous_cursor: str | None,
        bot_id: str | None, lifecycle_stage: str | None,
    ) -> RuntimeProbeResult:
        source = "openapi_bot.runtime"
        try:
            if self._bot is None:
                raise RuntimeError("Bot port is not configured")
            observation = await self._bot.get_session_runtime_status(
                session_id, bot_id=bot_id, lifecycle_stage=lifecycle_stage,
            )
        except Exception as exc:  # An unreadable activity path is not a dead Bot.
            return RuntimeProbeResult(
                state="activity_unknown", source=source,
                error=f"{type(exc).__name__}: {exc}"[:500],
                detail={"reason": "activity_query_failed"},
            )
        if not observation.found:
            return RuntimeProbeResult(
                state="activity_unknown", source=source,
                detail={"reason": observation.reason},
            )
        assert observation.last_event_seq is not None
        assert observation.state is not None
        identity = [observation.run_id, observation.started_at]
        cursor = "runtime:" + json.dumps(
            [identity, observation.last_event_seq], separators=(",", ":"),
        )
        previous = None
        if previous_cursor and previous_cursor.startswith("runtime:"):
            try:
                previous = json.loads(previous_cursor[len("runtime:"):])
            except (ValueError, TypeError):
                pass
        same_run = (
            isinstance(previous, list) and len(previous) == 2
            and previous[0] == identity and type(previous[1]) is int
        )
        baseline_reset = not same_run or observation.last_event_seq < previous[1]
        progressed = same_run and observation.last_event_seq > previous[1]
        states: dict[str, RuntimeLivenessState] = {
            "starting": "active", "running": "active", "final": "terminal_success",
            "error": "terminal_failed", "aborted": "terminal_failed",
        }
        return RuntimeProbeResult(
            state=states[observation.state], progressed=progressed, baseline_reset=baseline_reset,
            heartbeat_cursor=cursor, source=source, status=observation.state,
            detail={"engine_run_id": observation.run_id,
                    "last_event_seq": observation.last_event_seq,
                    "lifecycle_stage": lifecycle_stage},
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
        lifecycle_stage: str | None,
    ) -> RuntimeProbeResult:
        if self._bot is None:
            return RuntimeProbeResult(
                state="unknown",
                source="openapi_bot",
                detail={"reason": "port_missing"},
            )
        try:
            run = await self._bot.get_run(run_id)
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(
                source="openapi_bot.run",
                error=exc,
                detail={"run_id": run_id},
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
                    session_id,
                    limit=20,
                    bot_id=bot_id,
                    lifecycle_stage=lifecycle_stage,
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
                "lifecycle_stage": lifecycle_stage,
                "message_probe_error": message_error,
            },
        )

    def _group_bearer(
        self,
        *,
        group_id: str,
        group: dict[str, Any] | None,
        node: TaskNode,
    ) -> str | None:
        """Resolve a group participant token for the session-history ACL.

        The service HMAC identity is not a session participant. Prefer persisted
        driver facts, then the authoritative group's driver, and only then the
        node assignee. No token makes the transport fail closed without changing
        liveness semantics; the heartbeat read reports that transport error.
        """
        props = getattr(node.run_info, "extend_props", {}) or {}
        candidate = str(
            props.get("bcs_driver_bot")
            or props.get("assignee_bcs_uuid")
            or ""
        ).strip()
        if not candidate and isinstance(group, dict):
            candidate = str(
                group.get("driver_bot_id")
                or group.get("driver_bot")
                or ""
            ).strip()
        if not candidate:
            candidate = str(getattr(node.run_info, "assignee", "") or "").strip()
        if not candidate or candidate == group_id or self._bot_tokens is None:
            return None
        token = self._bot_tokens.get_token(candidate)
        return str(token or "") or None

    async def _probe_group(
        self,
        *,
        node: TaskNode,
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
        managed = (group or {}).get("group_strategy") == "manager_worker"
        if not managed and (group_state == "terminal_failed" or session_state == "terminal_failed"):
            return RuntimeProbeResult(
                state="terminal_failed",
                source="bcs.group",
                status=group_status or session_status,
                detail={"group_id": group_id, "run_error": run_error},
            )
        if not managed and (group_state == "terminal_success" or session_state == "terminal_success"):
            return RuntimeProbeResult(
                state="terminal_success",
                source="bcs.group",
                status=group_status or session_status,
                detail={"group_id": group_id, "run_error": run_error},
            )
        if group is None and run is None:
            # Neither collaboration runtime read produced an observation. Treat
            # this as the probe being unavailable rather than Bot liveness
            # evidence, so a service outage cannot consume Relay recovery.
            return RuntimeProbeResult(
                state="probe_unavailable",
                source="bcs",
                detail={
                    "reason": "probe_request_failed",
                    "group_id": group_id,
                    "run_id": run_id,
                },
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

        if managed and not session_id:
            return RuntimeProbeResult(
                state="activity_unknown", source="bcs.delivery",
                detail={"reason": "missing_session_id"},
            )
        cursor_value = _group_cursor(group)
        message_error: str | None = None
        if session_id:
            try:
                caller_bearer = self._group_bearer(
                    group_id=group_id,
                    group=group,
                    node=node,
                )
                managed = group.get("group_strategy") == "manager_worker"
                messages = await self._bcs.get_session_messages(
                    session_id,
                    limit=100 if managed else 20,
                    caller_bearer=caller_bearer,
                    **({"include_pending": True} if managed else {}),
                )
                if group.get("group_strategy") == "manager_worker":
                    from agentclaw.community.core.task.task_harness.group_delivery_probe import probe_group_deliveries

                    return await probe_group_deliveries(
                        self._bcs, node, messages=messages, caller_bearer=caller_bearer,
                        driver=str(group.get("driver_bot_id") or group.get("driver_bot") or ""),
                        previous_cursor=previous_cursor,
                    )
                cursor_value = (
                    cursor_value,
                    _last_message_cursor(messages),
                )
            except Exception as exc:  # noqa: BLE001 - heartbeat read is optional
                if group.get("group_strategy") == "manager_worker":
                    return RuntimeProbeResult(
                        state="activity_unknown", source="bcs.delivery",
                        error=f"{type(exc).__name__}: {exc}"[:500],
                        detail={"reason": "delivery_observation_unavailable"},
                    )
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
