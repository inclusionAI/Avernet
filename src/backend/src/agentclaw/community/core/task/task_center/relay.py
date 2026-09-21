"""Persistent relay-turn coordination stored with the task graph snapshot."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
)


@dataclass(frozen=True)
class RelayTurn:
    task_id: str
    node_id: str
    holder_id: str
    token: str
    expires_at_ms: int


class RelayCoordinator:
    """Issue and validate the single graph-backed lease for a relay holder.

    Only a token digest is persisted. Mutations reuse the graph repository's
    optimistic-version retry, preventing two replicas from consuming one turn.
    """

    def __init__(self, graph, *, ttl_seconds: int = 900) -> None:
        self._graph = graph
        self._ttl_ms = ttl_seconds * 1000

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_relay(graph) -> None:
        config = graph.extend_props.get("execution_config", {}) or {}
        if config.get("orchestration_mode") != "relay":
            raise TaskStateError(f"task={graph.task_id} is not in relay mode")

    def grant(
        self, task_id: str, node_id: str, holder_id: str, *, retry_event: bool = False
    ) -> RelayTurn | None:
        opaque_turn_value = secrets.token_urlsafe(32)
        expires_at = int(time.time() * 1000) + self._ttl_ms

        def mutation(graph):
            self._require_relay(graph)
            self._graph._require_node(graph, node_id)
            current = graph.extend_props.get("relay_turn") or {}
            current_node = self._graph._get_node(graph, node_id)
            terminal_graph = getattr(graph.status, "value", graph.status) in {
                "DONE",
                "SUCCESS",
                "HUNG",
                "FAILED",
                "CANCELLED",
            }
            terminal_node = getattr(
                getattr(current_node, "status", None), "value", None
            ) in {"DONE", "SUCCESS", "HUNG", "FAILED", "CANCELLED"}
            if retry_event and (
                current.get("node_id") != node_id
                or current.get("holder_id") != holder_id
                or current.get("status") == "CONSUMED"
                or terminal_graph
                or terminal_node
            ):
                return False, None, False
            active = current.get("status") == "GRANTED" and int(
                current.get("expires_at_ms", 0)
            ) > int(time.time() * 1000)
            if active:
                if (
                    current.get("node_id") != node_id
                    or current.get("holder_id") != holder_id
                ):
                    raise TaskStateError(f"relay turn already granted task={task_id}")
                # A retried EXECUTION_RESULT must return a usable token without
                # invalidating the token from the first response. Persist only
                # digests; either response can consume the single shared turn.
                digests = self._token_digests(current)
                digests.append(self._digest(opaque_turn_value))
                current["token_digests"] = digests[-4:]
                current["expires_at_ms"] = expires_at
                graph.extend_props["relay_turn"] = current
                return True, None, True
            graph.extend_props["relay_turn"] = {
                "node_id": node_id,
                "holder_id": holder_id,
                "token_digests": [self._digest(opaque_turn_value)],
                "status": "GRANTED",
                "expires_at_ms": expires_at,
            }
            return True, None, True

        issued = self._graph._mutate_with_version_retry(task_id, mutation)
        if not issued:
            return None
        return RelayTurn(task_id, node_id, holder_id, opaque_turn_value, expires_at)

    @staticmethod
    def _token_digests(current: dict) -> list[str]:
        """Read the bounded digest set, including pre-upgrade snapshots."""
        values = [str(value) for value in current.get("token_digests") or [] if value]
        legacy = current.get("token_digest")
        if legacy and str(legacy) not in values:
            values.append(str(legacy))
        return values

    def _token_matches(self, current: dict, token: str) -> bool:
        digest = self._digest(token)
        return any(
            hmac.compare_digest(value, digest) for value in self._token_digests(current)
        )

    def require(self, task_id: str, node_id: str, holder_id: str, token: str) -> str:
        graph = self._graph.query_task_dashboard(task_id)
        self._require_relay(graph)
        current = graph.extend_props.get("relay_turn") or {}
        valid = (
            current.get("status") == "GRANTED"
            and int(current.get("expires_at_ms", 0)) > int(time.time() * 1000)
            and current.get("holder_id") == holder_id
            and self._token_matches(current, token)
        )
        if not valid:
            raise TaskStateError(f"relay turn invalid task={task_id}")
        return str(current.get("node_id") or "")

    def consume(self, task_id: str, node_id: str, holder_id: str, token: str) -> None:
        digest = self._digest(token)

        def mutation(graph):
            self._require_relay(graph)
            current = graph.extend_props.get("relay_turn") or {}
            valid = (
                current.get("status") == "GRANTED"
                and int(current.get("expires_at_ms", 0)) > int(time.time() * 1000)
                and current.get("holder_id") == holder_id
                and any(
                    hmac.compare_digest(value, digest)
                    for value in self._token_digests(current)
                )
            )
            if not valid:
                raise TaskStateError(f"relay turn cannot be consumed task={task_id}")
            current["status"] = "CONSUMED"
            current["consumed_at_ms"] = int(time.time() * 1000)
            graph.extend_props["relay_turn"] = current
            return None, None, True

        self._graph._mutate_with_version_retry(task_id, mutation)

    def renew_expired(
        self, task_id: str, node_id: str, holder_id: str
    ) -> RelayTurn | None:
        """Renew an expired current-baton lease for a resume prompt.

        This only updates graph-level relay control metadata. It never changes a
        node, so a stalled baton cannot mutate any predecessor while recovering.
        """
        opaque_turn_value = secrets.token_urlsafe(32)
        expires_at = int(time.time() * 1000) + self._ttl_ms

        def mutation(graph):
            self._require_relay(graph)
            current = graph.extend_props.get("relay_turn") or {}
            expired_current_turn = (
                current.get("status") == "GRANTED"
                and current.get("node_id") == node_id
                and current.get("holder_id") == holder_id
                and int(current.get("expires_at_ms", 0)) <= int(time.time() * 1000)
            )
            if not expired_current_turn:
                return False, None, False
            current["token_digests"] = [self._digest(opaque_turn_value)]
            current["status"] = "GRANTED"
            current["expires_at_ms"] = expires_at
            current["resumed_at_ms"] = int(time.time() * 1000)
            graph.extend_props["relay_turn"] = current
            return True, None, True

        renewed = self._graph._mutate_with_version_retry(task_id, mutation)
        if not renewed:
            return None
        return RelayTurn(task_id, node_id, holder_id, opaque_turn_value, expires_at)

    def reopen(self, task_id: str, holder_id: str, token: str) -> None:
        """Restore the same lease after a reserved dispatch fails to deliver."""
        digest = self._digest(token)

        def mutation(graph):
            current = graph.extend_props.get("relay_turn") or {}
            if (
                current.get("status") != "CONSUMED"
                or current.get("holder_id") != holder_id
                or not any(
                    hmac.compare_digest(value, digest)
                    for value in self._token_digests(current)
                )
            ):
                raise TaskStateError(f"relay turn cannot be reopened task={task_id}")
            current["status"] = "GRANTED"
            current["expires_at_ms"] = int(time.time() * 1000) + self._ttl_ms
            graph.extend_props["relay_turn"] = current
            return None, None, True

        self._graph._mutate_with_version_retry(task_id, mutation)

    def begin_event(self, task_id: str, event_key: str) -> dict:
        """Atomically reserve a relay event across instances.

        ``seen_event`` followed by a later mutation is racy: two replicas can
        both observe a missing event.  The reservation is stored in the same
        optimistic graph snapshot and expires so a crashed owner can be
        retried.
        """
        now = int(time.time() * 1000)

        def mutation(graph):
            records = dict(graph.extend_props.get("relay_event_records") or {})
            current = records.get(event_key) or {}
            state = str(current.get("state") or "")
            claimed_at = int(current.get("claimed_at_ms") or 0)
            if state == "COMPLETED":
                return {"state": state, "result": current.get("result")}, None, False
            if state == "PROCESSING" and now - claimed_at < self._ttl_ms:
                return {"state": state}, None, False
            records[event_key] = {"state": "PROCESSING", "claimed_at_ms": now}
            graph.extend_props["relay_event_records"] = records
            return {"state": "CLAIMED"}, None, True

        return self._graph._mutate_with_version_retry(task_id, mutation)

    def complete_event(
        self, task_id: str, event_key: str, result: dict
    ) -> None:
        """Commit a previously reserved event without persisting bearer tokens."""

        def mutation(graph):
            records = dict(graph.extend_props.get("relay_event_records") or {})
            current = dict(records.get(event_key) or {})
            persisted_result = {
                key: value
                for key, value in result.items()
                if key not in {"relay_turn", "dispatch_turn"}
            }
            current.update(
                {
                    "state": "COMPLETED",
                    "result": persisted_result,
                    "completed_at_ms": int(time.time() * 1000),
                }
            )
            records[event_key] = current
            graph.extend_props["relay_event_records"] = records
            graph.extend_props["relay_event_ids"] = (
                list(graph.extend_props.get("relay_event_ids") or []) + [event_key]
            )[-200:]
            return None, None, True

        self._graph._mutate_with_version_retry(task_id, mutation)

    def release_event(self, task_id: str, event_key: str) -> None:
        """Release a failed reservation so the caller can retry safely."""

        def mutation(graph):
            records = dict(graph.extend_props.get("relay_event_records") or {})
            if event_key not in records:
                return None, None, False
            records.pop(event_key, None)
            graph.extend_props["relay_event_records"] = records
            return None, None, True

        self._graph._mutate_with_version_retry(task_id, mutation)

    def expire_turn(self, task_id: str, node_id: str, holder_id: str) -> bool:
        """Atomically close an expired relay lease for recovery exhaustion."""
        now = int(time.time() * 1000)

        def mutation(graph):
            current = dict(graph.extend_props.get("relay_turn") or {})
            if (
                current.get("status") != "GRANTED"
                or current.get("node_id") != node_id
                or current.get("holder_id") != holder_id
                or int(current.get("expires_at_ms") or 0) > now
            ):
                return False, None, False
            current["status"] = "EXPIRED"
            current["expired_at_ms"] = now
            graph.extend_props["relay_turn"] = current
            return True, None, True

        return bool(self._graph._mutate_with_version_retry(task_id, mutation))

    def seen_event(self, task_id: str, event_id: str) -> bool:
        graph = self._graph.query_task_dashboard(task_id)
        return event_id in (graph.extend_props.get("relay_event_ids") or [])

    def mark_event(self, task_id: str, event_id: str) -> None:
        def mutation(graph):
            ids = list(graph.extend_props.get("relay_event_ids") or [])
            if event_id in ids:
                return None, None, False
            graph.extend_props["relay_event_ids"] = (ids + [event_id])[-200:]
            return None, None, True

        self._graph._mutate_with_version_retry(task_id, mutation)


logger = logging.getLogger("task.relay.callback")
_MAX_DIAGNOSTIC_TEXT = 2000


def _diagnostic_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= _MAX_DIAGNOSTIC_TEXT:
        return text
    return f"{text[:_MAX_DIAGNOSTIC_TEXT]}...(truncated)"


def relay_attempt(service: Any, task_id: str) -> int:
    try:
        return int(service._graph.query_task_dashboard(task_id).loop_round or 0)
    except Exception:  # noqa: BLE001 diagnostics only
        return 0


def emit_relay_event(
    service: Any,
    *,
    task_id: str,
    node_id: str,
    action_result: str,
    error_type: ReasonCatalog | None = None,
    error_msg: str | None = None,
    ext_info: dict[str, Any] | None = None,
    status_from: Any = None,
    status_to: Any = None,
    attempt: int = 0,
    boost_reason: str | None = None,
) -> None:
    """Emit Relay trajectory evidence without affecting task progression."""
    context_service = getattr(service, "_task_context_service", None)
    if context_service is None:
        return
    try:
        context_service.emit_trajectory_event(
            task_id,
            node_id,
            "relay",
            action_result=action_result,
            error_type=error_type,
            error_msg=error_msg,
            ext_info=ext_info,
            status_from=status_from,
            status_to=status_to,
            attempt=attempt,
            boost_reason=boost_reason,
        )
    except Exception as exc:  # noqa: BLE001 diagnostic path must not block Relay
        logger.warning(
            "[task][relay][trajectory] task=%s node=%s result=%s failed: %s",
            task_id,
            node_id,
            action_result,
            exc,
        )


def emit_relay_callback_success(
    service: Any,
    *,
    task_id: str,
    node_id: str,
    event_type: str,
    event_id: str,
    holder_id: str,
    relay_turn: str | None,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Append transport-level evidence that a relay callback was applied."""
    attempt = 0
    try:
        graph = service._graph.query_task_dashboard(task_id)
        attempt = int(getattr(graph, "loop_round", 0) or 0)
    except Exception:  # noqa: BLE001 diagnostics must not affect the callback
        pass
    service._emit_relay(
        task_id=task_id,
        node_id=node_id,
        action_result="callback_reported",
        attempt=attempt,
        ext_info={
            "event_type": event_type,
            "event_id": event_id,
            "holder_id": holder_id,
            "relay_turn_prefix": str(relay_turn or "")[:8],
            "payload_keys": sorted(str(key) for key in payload.keys()),
            "idempotent": bool(result.get("idempotent", False)),
            "completed": bool(result.get("completed", False)),
            "hung": bool(result.get("hung", False)),
            "published_bbs": bool(result.get("published_bbs", False)),
            "turn_consumed": bool(result.get("turn_consumed", False)),
            "relay_turn_granted": bool(result.get("relay_turn")),
        },
    )


def emit_relay_callback_error(
    service: Any,
    *,
    task_id: str,
    node_id: str,
    event_type: str,
    event_id: str,
    holder_id: str,
    relay_turn: str | None,
    progress_reason: str | None,
    failure_reason: str | None,
    payload: dict[str, Any] | None,
    error_phase: str,
    exception_type: str,
    error_msg: str,
) -> None:
    """Append a correlated relay callback failure without changing callback semantics."""
    correlated_task_id = str(task_id or "").strip()
    correlated_node_id = str(node_id or correlated_task_id).strip()
    if not correlated_task_id or not correlated_node_id:
        logger.warning(
            "[task][relay][callback] 无法关联异常轨迹 phase=%s event_type=%s "
            "event_id=%s exception=%s error=%s",
            error_phase,
            event_type,
            event_id,
            exception_type,
            _diagnostic_text(error_msg),
        )
        return

    attempt = 0
    try:
        graph = service._graph.query_task_dashboard(correlated_task_id)
        attempt = int(getattr(graph, "loop_round", 0) or 0)
    except Exception:  # noqa: BLE001 diagnostics must not mask the callback error
        pass

    try:
        service._emit_relay(
            task_id=correlated_task_id,
            node_id=correlated_node_id,
            action_result="callback_report_failed",
            error_type=ReasonCatalog.RELAY,
            error_msg=_diagnostic_text(error_msg),
            attempt=attempt,
            ext_info={
                "error_phase": error_phase,
                "exception_type": exception_type,
                "event_type": str(event_type or ""),
                "event_id": str(event_id or ""),
                "holder_id": str(holder_id or ""),
                "reported_node_id": str(node_id or ""),
                "relay_turn_prefix": str(relay_turn or "")[:8],
                "progress_reason": _diagnostic_text(progress_reason),
                "failure_reason": _diagnostic_text(failure_reason),
                "payload_keys": sorted(str(key) for key in (payload or {}).keys()),
            },
        )
    except Exception as exc:  # noqa: BLE001 fire-and-forget diagnostic path
        logger.warning(
            "[task][relay][callback] 异常轨迹发射失败 task=%s node=%s phase=%s: %s",
            correlated_task_id,
            correlated_node_id,
            error_phase,
            exc,
            exc_info=True,
        )
