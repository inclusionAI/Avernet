"""ActiveRunRegistry — OpenClaw-owned in-flight chat-run registry.

Tracks active ``chat_stream`` runs as they happen; the read-only
``GET /api/engine/active-sessions`` endpoint asks the OpenClaw engine to
query this registry, which returns a dual-axis
``{query_status, verdict}`` response.

This is OpenClaw's OWN runtime state, derived from OpenClaw's own
chat-stream execution chain (the ``gateway_client.chat_stream`` run
lifecycle). It deliberately does NOT consult Claude Code's
``session-runtime-registry`` (that is the Claude Code gateway's own
bookkeeping, scoped to a different engine), AICoding's ``RunStatusService``
(which rides on AICoding workspace state), session file mtime, or
``active_connections`` (idle WebSocket count). None of those can authoritatively
answer "does OpenClaw have a session/run in flight right now?" — only the
OpenClaw runtime can, so the registry records that ground truth.
"""
from __future__ import annotations

import logging
import uuid
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("openclaw-active-run-registry")

#: OpenClaw chat_stream has a built-in 20-minute timeout by default
#: (``gateway_client._DEFAULT_CHAT_STREAM_TIMEOUT_SECONDS``). A run that has
#: been silent longer than that almost certainly never reached us with a
#: terminal event (subprocess died, socket broke mid-flight, ...) — we can't
#: safely classify such an entry as live. Treating it as "incomplete" maps to
#: ``verdict=unknown`` per the active-sessions behavior matrix.
DEFAULT_STALE_AFTER_SECONDS = 1800.0  # 30 min — slightly above chat timeout

def _now() -> datetime:
    return datetime.now(tz=UTC)


def _to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def canonicalize_session_id(session_key: str | None) -> str:
    """Reduce an OpenClaw session key to its canonical session id.

    OpenClaw session keys vary in shape, e.g. ``session:<uuid>:user:<uid>``
    or ``agent:main:session:<uuid>:user:<uid>`` when an agent alias is
    folded in. The canonical id shared by every alias of the same parent
    session is the uuid part. For unexpected shapes we fall back to the
    raw (trimmed) key rather than manufacturing one. Never raises.
    """
    if not session_key or not isinstance(session_key, str):
        return ""
    text = session_key.strip()
    if not text:
        return ""
    if text.lower().startswith("agent:"):
        # Drop "agent:<agent-name>:" prefix (parent session follows).
        seg = text.split(":", 2)
        if len(seg) >= 3 and seg[0].lower() == "agent":
            text = seg[2]
    parts = text.split(":")
    if len(parts) >= 2 and parts[0] in {"session", "svc-session"} and parts[1]:
        return parts[1]
    return text


def derive_agent_id(session_key: str | None) -> str | None:
    """Best-effort OpenClaw agent id from a session key.

    Returns the agent-name segment when the key starts with
    ``agent:<name>:`` (e.g. ``agent:main:`` → ``main``), or ``None`` when no
    agent alias is present (parent/default session).
    """
    if not session_key or not isinstance(session_key, str):
        return None
    text = session_key.strip()
    if not text.lower().startswith("agent:"):
        return None
    seg = text[len("agent:"):].split(":", 1)
    name = seg[0] if seg else ""
    return name or None


@dataclass
class ActiveRunEntry:
    """A single in-flight chat_stream run on this OpenClaw engine.

    ``call_id`` + ``session_key`` are guaranteed non-empty at registration;
    ``run_id`` and ``agent_id`` are best-effort and may be filled as events
    arrive (the gateway surfaces ``runId`` in payloads).
    """

    call_id: str
    session_key: str
    started_at: datetime
    updated_at: datetime
    run_id: str | None = None
    agent_id: str | None = None
    last_state: str = "running"
    last_event_at: datetime | None = None
    # Used by tests/inspectors to distinguish a released entry from a live one
    # without poking the registry's internal dict. Mutated only via `release()`.
    terminal: bool = False

    def touch(
        self,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        last_state: str | None = None,
        last_event_at: datetime | None = None,
    ) -> None:
        now = _now()
        self.updated_at = now
        self.last_event_at = last_event_at or now
        if run_id:
            self.run_id = run_id
        if agent_id:
            self.agent_id = agent_id
        if last_state:
            self.last_state = last_state

    def to_session_dict(self) -> dict[str, Any]:
        return {
            "session_id": canonicalize_session_id(self.session_key),
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "started_at": _to_iso(self.started_at),
            "updated_at": _to_iso(self.updated_at),
            "last_state": self.last_state,
            "last_event_at": _to_iso(self.last_event_at) if self.last_event_at else None,
        }


class ActiveRunRegistry:
    """OpenClaw in-flight chat-run registry.

    Single-threaded asyncio; dict mutation is safe without a lock because each
    write completes synchronously on the loop and the registry isn't shared
    across OS threads. If/when cross-thread access is added, callers take their
    own lock around this surface; the registry never spawns threads.
    """

    def __init__(self, *, stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS) -> None:
        self._entries: dict[str, ActiveRunEntry] = {}
        self._stale_after = max(0.0, float(stale_after_seconds))

    # ── mutation surface (called by the OpenClaw chat port) ─────────────────

    def new_call_id(self) -> str:
        """Convenience UUID generator used by the chat port to mint a call_id
        when the caller-provided idempotency_key context isn't guaranteed to be
        present/unique per stream."""
        return uuid.uuid4().hex

    def register(
        self,
        call_id: str,
        session_key: str,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> ActiveRunEntry:
        now = _now()
        entry = ActiveRunEntry(
            call_id=call_id,
            session_key=session_key,
            started_at=now,
            updated_at=now,
            last_event_at=now,
            run_id=run_id,
            agent_id=agent_id or derive_agent_id(session_key),
            last_state="running",
            terminal=False,
        )
        self._entries[call_id] = entry
        log.info(
            "active_run_registry register call_id=%s session_id=%s run_id=%s",
            call_id,
            canonicalize_session_id(session_key),
            run_id or "<unknown>",
        )
        return entry

    def touch(self, call_id: str, **kwargs: Any) -> bool:
        entry = self._entries.get(call_id)
        if entry is None or entry.terminal:
            return False
        entry.touch(**kwargs)
        return True

    def release(self, call_id: str, *, last_state: str = "completed") -> None:
        """Mark a run terminal and remove it from the active set.

        Removed synchronously so a query landing right after the stream ends
        sees verdict=clear (or whatever the remaining live set says). Safe to
        call for an already-released/unknown call_id (no-op).
        """
        entry = self._entries.pop(call_id, None)
        if entry is None:
            return
        entry.terminal = True
        entry.last_state = last_state
        log.info(
            "active_run_registry release call_id=%s session_id=%s last_state=%s",
            entry.call_id,
            canonicalize_session_id(entry.session_key),
            last_state,
        )

    # ── query surface (read by the endpoint via OpenClawEngine) ──────────────

    def list_active_sessions(self) -> tuple[list[dict[str, Any]], int]:
        """Return ``(active_session_dicts, stale_count)``.

        ``stale_count`` > 0 means at least one entry's last update is older
        than ``stale_after_seconds`` — the registry could not observe the
        terminal transition. The caller MUST surface that as an incomplete
        result and return ``query_status=error, verdict=unknown`` per the active-sessions behavior matrix
        # active-sessions behavior matrix
        behavior matrix. Stale entries are NOT included in the live sessions
        list (they would otherwise risk a false ``active`` verdict).
        """
        now = _now()
        live: list[dict[str, Any]] = []
        stale_count = 0
        for entry in self._entries.values():
            if entry.terminal:
                continue
            age_s = (now - entry.updated_at).total_seconds()
            if age_s >= self._stale_after:
                stale_count += 1
            else:
                live.append(entry.to_session_dict())
        return live, stale_count

    def active_count(self) -> int:
        live, _ = self.list_active_sessions()
        return len(live)

    def snapshot(self) -> dict[str, Any]:
        """All entries (active + stale + terminal) — tests/inspectors only."""
        return {
            call_id: entry.to_session_dict()
            for call_id, entry in self._entries.items()
        }

    async def list_active_sessions_async(self) -> tuple[list[dict[str, Any]], int]:
        """Async variant with a single event-loop yield so ``asyncio.wait_for``
        can observe a real scheduling boundary and abort via ``TimeoutError``.
        Thin wrapper over the sync ``list_active_sessions``.
        """
        await asyncio.sleep(0)
        return self.list_active_sessions()


__all__ = [
    "ActiveRunEntry",
    "ActiveRunRegistry",
    "DEFAULT_STALE_AFTER_SECONDS",
    "canonicalize_session_id",
    "derive_agent_id",
]
