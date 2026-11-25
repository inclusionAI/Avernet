"""Request-scoped identities for accepted OpenClaw child executions."""

from dataclasses import dataclass
import logging
import time
from typing import Any

log = logging.getLogger("openclaw-client")
_TERMINAL_STATES = ("final", "error", "aborted")
_MAX_EARLY_TERMINALS = 128


def is_child_event(event_name: str, payload: dict[str, Any]) -> bool:
    return (
        event_name in ("chat", "agent")
        and isinstance(payload.get("sessionKey"), str)
        and ":subagent:" in payload["sessionKey"]
    )


def is_parent_continuation(event_name: str, payload: dict[str, Any]) -> bool:
    data = payload.get("data")
    return (event_name == "chat" and payload.get("state") == "delta") or (
        event_name == "agent" and (
            payload.get("stream") in ("assistant", "tool", "item")
            or (payload.get("stream") == "lifecycle"
                and isinstance(data, dict) and data.get("phase") == "start")
        )
    )


@dataclass
class ChildRun:
    session_key: str
    completed: bool = False
    announced: bool = False
    parent_covered: bool = False
    compaction_finals: int = 0
    compaction_ends: int = 0


class ChildRuns:
    """Keep completed identities too, so duplicate frames never settle twice."""

    def __init__(self, parent_session_key: str) -> None:
        self._parent_session_key = parent_session_key
        self._started = time.monotonic()
        self._parent_session = parent_session_key.strip().lower().removeprefix("agent:main:")
        self.runs: dict[str, ChildRun] = {}
        self._early_runs: dict[tuple[str, str], ChildRun] = {}

    def _log(self, message: str, *args: Any) -> None:
        log.info(
            "system=openclaw direction=inbound operation=child_tracking "
            "elapsedMs=%.3f parentSessionKey=%s " + message,
            (time.monotonic() - self._started) * 1000,
            self._parent_session_key, *args,
        )

    def matches_parent(self, payload: dict[str, Any]) -> bool:
        candidate = payload.get("sessionKey")
        return isinstance(candidate, str) and (
            candidate.strip().lower().removeprefix("agent:main:") == self._parent_session
        )

    @property
    def pending_count(self) -> int:
        return sum(not child.completed for child in self.runs.values())

    @property
    def unannounced_count(self) -> int:
        return sum(not child.announced and not child.parent_covered for child in self.runs.values())

    def cover_completed(self) -> None:
        """A subsequent parent final covers already finished direct children."""
        for child in self.runs.values():
            if child.completed:
                child.parent_covered = True

    @property
    def has_completed(self) -> bool:
        return any(child.completed for child in self.runs.values())

    def register(self, run_id: str, session_key: str) -> None:
        existing = self.runs.get(run_id)
        if existing is not None:
            self._log(
                "[chat_stream][child_spawn_duplicate] childRunId=%s sessionKey=%s matches=%s",
                run_id, session_key, existing.session_key == session_key,
            )
            return
        child = self._early_runs.pop((run_id, session_key), ChildRun(session_key))
        self.runs[run_id] = child
        self._log(
            "[chat_stream][sessions_spawn_accepted] childRunId=%s sessionKey=%s "
            "completed=%s pendingAnnounces=%d",
            run_id, session_key, child.completed, self.pending_count,
        )

    def observe_event(self, event_name: str, payload: dict[str, Any]) -> bool:
        run_id = payload.get("runId")
        session_key = payload.get("sessionKey")
        if not isinstance(run_id, str) or not isinstance(session_key, str):
            return False
        child = self.runs.get(run_id)
        if child is None:
            if (session_key.strip().lower().removeprefix("agent:main:") == self._parent_session
                    or not is_child_event(event_name, payload)):
                return False
            # COSEC: Provisional state is bounded and cannot authorize a run.
            # Preserve compaction too, so an early retry final is not terminal.
            key = (run_id, session_key)
            if key not in self._early_runs:
                if len(self._early_runs) >= _MAX_EARLY_TERMINALS:
                    self._early_runs.pop(next(iter(self._early_runs)))
                self._early_runs[key] = ChildRun(session_key)
            child = self._early_runs[key]
        # COSEC: A run ID alone cannot authorize a cross-session terminal.
        if session_key != child.session_key:
            self._log(
                "[chat_stream][child_terminal_rejected] childRunId=%s sessionKey=%s",
                run_id, session_key,
            )
            return True
        data = payload.get("data")
        phase = data.get("phase") if isinstance(data, dict) else None
        stream = payload.get("stream")
        if event_name == "agent":
            if stream == "compaction" and phase == "end" and data.get("willRetry") is True:
                child.compaction_finals += 1
            elif stream == "lifecycle" and phase == "end":
                if child.compaction_ends < child.compaction_finals:
                    child.compaction_ends += 1
            elif is_parent_continuation(event_name, payload):
                child.compaction_finals = child.compaction_ends
            return True
        if event_name != "chat" or payload.get("state") not in _TERMINAL_STATES:
            if is_parent_continuation(event_name, payload):
                child.compaction_finals = child.compaction_ends
            return True
        if payload["state"] == "final" and child.compaction_ends > 0:
            child.compaction_finals -= 1
            child.compaction_ends -= 1
            self._log(
                "[chat_stream][child_compaction_final_suppressed] childRunId=%s sessionKey=%s",
                run_id, session_key,
            )
            return True
        duplicate = child.completed
        child.completed = True
        self._log(
            "[chat_stream][child_terminal] childRunId=%s sessionKey=%s "
            "state=%s duplicate=%s pendingChildren=%d",
            run_id, session_key, payload["state"], duplicate, self.pending_count,
        )
        return True

    def announce_child(self, announce_run_id: str) -> ChildRun | None:
        for run_id, child in self.runs.items():
            if announce_run_id == f"announce:v1:{child.session_key}:{run_id}":
                return child
        return None

    def complete_announce(self, announce_run_id: str) -> bool:
        child = self.announce_child(announce_run_id)
        if child is None or child.announced:
            self._log(
                "[chat_stream][child_announce_ignored] runId=%s known=%s",
                announce_run_id, child is not None,
            )
            return False
        child.completed = True
        child.announced = True
        self._log(
            "[chat_stream][child_announce_final] runId=%s sessionKey=%s "
            "pendingChildren=%d pendingAnnounces=%d",
            announce_run_id, child.session_key, self.pending_count, self.unannounced_count,
        )
        return True
