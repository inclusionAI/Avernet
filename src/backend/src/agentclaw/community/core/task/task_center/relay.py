"""Persistent relay-turn coordination stored with the task graph snapshot."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from agentclaw.community.core.task.domain.errors import TaskStateError


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
            if retry_event and (
                current.get("node_id") != node_id
                or current.get("holder_id") != holder_id
                or current.get("status") == "CONSUMED"
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
        return any(hmac.compare_digest(value, digest) for value in self._token_digests(current))

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

    def renew_expired(self, task_id: str, node_id: str, holder_id: str) -> RelayTurn | None:
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
