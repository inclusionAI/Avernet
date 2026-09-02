"""OpenClaw active-run registry — process-local runtime state for the
read-only Active Session query API (OpenClaw engine 侧).

The OpenClaw gateway owns the authoritative run state upstream, but the engine
itself historically had no readable per-session/per-run "is a run in flight"
view: ``/api/sessions`` ``status=active`` only marks the session record's
lifecycle, and ``/api/engine/status`` ``active_connections`` only counts
WebSocket connections. Neither can prove there is (or isn't) a running chat
``run``.

Rather than fake a precise verdict from those proxies (or from the
Claude-Code / AICoding registries, which belong to other engines), this module
implements OpenClaw's *own* minimal runtime record: an in-process registry of
chat runs observed through the single OpenClaw chat entry point
(:class:`~engine.community.core.adapters.openclaw.chat.OpenClawChatAdapter`).
The registry is updated as a side-channel of the chat stream (register on the
first frame carrying a ``runId``; mark terminal on ``final`` / ``error`` /
``aborted`` and in a ``finally`` safety net) and read by the read-only
``GET /api/engine/active-sessions`` endpoint.

Concurrency: all mutation/reads run inside the single asyncio event loop with
no internal ``await`` points, so plain dict access is atomic without a lock.
``query`` is async only so a caller-query timeout can be enforced via
``asyncio.wait_for`` (maps to ``query_status=timeout`` / ``verdict=unknown``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger("openclaw-active-run-registry")

# Run lifecycle states the registry records. Only ``running`` counts as
# "active" for the verdict; the terminal states are retained so the lifecycle
# tests can confirm a finished run no longer reports as active, then age off
# via ``max_retained_terminal``.
RunState = Literal["running", "completed", "failed", "aborted"]
RUNNING_STATE: RunState = "running"
_TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "aborted"})


@dataclass
class ActiveRun:
    """One observed OpenClaw chat run.

    ``session_key`` is the OpenClaw session identifier surfaced as
    ``session_id`` in the public response (consistent with ``Session.id=key``
    in the session ACL adapter).
    """

    run_id: str
    session_key: str
    agent_id: str | None
    state: RunState
    started_at: datetime
    updated_at: datetime


class ActiveSessionEntry(BaseModel):
    """Public entry in an Active Session query response."""

    session_id: str
    run_id: str
    state: RunState
    started_at: datetime
    updated_at: datetime
    agent_id: str | None = None


class ActiveSessionQueryResult(BaseModel):
    """Public response for ``GET /api/engine/active-sessions``.

    ``query_status`` describes the query itself; ``verdict`` is the business
    conclusion and is only ``clear``/``active`` when ``query_status=ok``.
    """

    query_status: Literal["ok", "unsupported", "timeout", "error"]
    verdict: Literal["clear", "active", "unknown"]
    engine: str
    checked_at: datetime
    count: int
    sessions: list[ActiveSessionEntry] = Field(default_factory=list)
    reason: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _ok_result(
    engine: str,
    checked_at: datetime,
    runs: list[ActiveRun],
    *,
    reason: str | None = None,
) -> ActiveSessionQueryResult:
    entries: list[ActiveSessionEntry] = []
    if reason is None:
        entries = [
            ActiveSessionEntry(
                session_id=r.session_key,
                run_id=r.run_id,
                state=r.state,
                started_at=r.started_at,
                updated_at=r.updated_at,
                agent_id=r.agent_id,
            )
            for r in runs
        ]
    # When `reason` is set (e.g. incomplete), the raw runs cannot be reliably
    # associated to sessions, so we do NOT surface them — only the verdict and
    # reason communicate the degraded state.
    verdict: Literal["clear", "active", "unknown"]
    if reason is not None:
        verdict = "unknown"
    elif runs:
        verdict = "active"
    else:
        verdict = "clear"
    return ActiveSessionQueryResult(
        query_status="ok",
        verdict=verdict,
        engine=engine,
        checked_at=checked_at,
        count=len(entries),
        sessions=entries,
        reason=reason,
    )


class ActiveRunRegistry:
    """Process-local registry of OpenClaw chat runs.

    Owned by :class:`~engine.community.engines.openclaw.engine.OpenClawEngine`
    and injected into the chat adapter. Mutations happen inline on the chat
    hot path with no ``await`` between read and write, so they are atomic
    under the single-threaded asyncio loop.
    """

    def __init__(self, max_retained_terminal: int = 256) -> None:
        self._runs: dict[str, ActiveRun] = {}
        # Insertion order of terminal runs, for bounded retention so finished
        # runs don't accumulate without limit.
        self._terminal_order: list[str] = []
        self._max_retained_terminal = max(0, max_retained_terminal)

    # ── Mutation surface (called inline by the chat adapter) ──────────────

    def register_run(
        self,
        run_id: str,
        session_key: str,
        *,
        agent_id: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        """Record (or refresh) a run as ``running``.

        Idempotent for the same ``run_id``: a re-register of an already-running
        run refreshes ``updated_at`` only. Re-registering a terminal run is a
        no-op (a terminal run must not flip back to running) — this preserves
        the "finished run is never active again" invariant.
        """
        if not run_id or not session_key:
            return
        now = started_at or _now()
        existing = self._runs.get(run_id)
        if existing is not None:
            if existing.state != RUNNING_STATE:
                return
            existing.updated_at = now
            existing.agent_id = agent_id or existing.agent_id
            return
        self._runs[run_id] = ActiveRun(
            run_id=run_id,
            session_key=session_key,
            agent_id=agent_id,
            state=RUNNING_STATE,
            started_at=now,
            updated_at=now,
        )

    def rebind_run(self, old_run_id: str, new_run_id: str) -> bool:
        """Replace a provisional run id with the gateway-assigned id.

        The adapter registers the idempotency key before entering the stream so
        silent runs are visible. If a gateway frame later carries a different
        run id, merge the provisional record instead of exposing two active
        entries for one execution.
        """
        if not old_run_id or not new_run_id or old_run_id == new_run_id:
            return old_run_id == new_run_id
        run = self._runs.pop(old_run_id, None)
        if run is None:
            return False
        existing = self._runs.get(new_run_id)
        if existing is not None:
            # Keep the record already associated with the gateway id and avoid
            # manufacturing a duplicate.
            self._runs[old_run_id] = run
            return False
        run.run_id = new_run_id
        self._runs[new_run_id] = run
        return True

    def mark_terminal(
        self,
        run_id: str,
        state: RunState,
        *,
        updated_at: datetime | None = None,
    ) -> None:
        """Mark a run terminal (``completed`` / ``failed`` / ``aborted``).

        Silently ignores unknown run ids, non-terminal states, and runs that
        are *already* terminal (the first terminal state wins, so the loop's
        explicit terminal frame is not overwritten by the ``finally`` safety
        net). This lets the chat adapter call this unconditionally from both
        the streaming loop and its ``finally`` block.
        """
        if not run_id or state not in _TERMINAL_STATES:
            return
        run = self._runs.get(run_id)
        if run is None or run.state in _TERMINAL_STATES:
            return
        now = updated_at or _now()
        run.state = state
        run.updated_at = now
        self._terminal_order.append(run_id)
        self._evict_terminal()

    def _evict_terminal(self) -> None:
        cap = self._max_retained_terminal
        while len(self._terminal_order) > cap and self._terminal_order:
            rid = self._terminal_order.pop(0)
            run = self._runs.get(rid)
            if run is not None and run.state in _TERMINAL_STATES:
                del self._runs[rid]

    # ── Read surface (called by the query path) ───────────────────────────

    def snapshot(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[ActiveRun]:
        """Return active (``running``) runs, optionally filtered.

        Filtering is exact-match on ``session_id`` (OpenClaw session key) and
        ``agent_id``. ``agent_id`` filtering never matches a run whose
        ``agent_id`` is unset, so querying by agent on the OpenClaw path
        (which does not populate ``agent_id``) yields an empty result — that
        is the documented, safe semantics.
        """
        out: list[ActiveRun] = []
        for run in self._runs.values():
            if run.state != RUNNING_STATE:
                continue
            if session_id is not None and run.session_key != session_id:
                continue
            if agent_id is not None and run.agent_id != agent_id:
                continue
            out.append(run)
        return out

    async def query(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        timeout: float | None = None,
        engine: str = "openclaw",
    ) -> ActiveSessionQueryResult:
        """Build the dual-axis query result.

        ``timeout`` (seconds) bounds the snapshot; exceeding it returns
        ``query_status=timeout``. Snapshot exceptions return ``error``. A
        snapshot containing an entry that cannot be associated with a session
        / run (both identifiers must be non-empty) returns ``unknown`` with
        ``reason="incomplete"``. Otherwise ``clear`` (no active runs) or
        ``active`` (>= 1 active run).
        """
        checked_at = _now()

        async def _do_snapshot() -> list[ActiveRun]:
            result = self.snapshot(session_id=session_id, agent_id=agent_id)
            # ``snapshot`` is synchronous in production; tests (and any future
            # async source) may return a coroutine, which we await here so the
            # surrounding ``asyncio.wait_for`` can enforce a real timeout.
            if asyncio.iscoroutine(result):
                result = await result  # type: ignore[assignment]
            return result  # type: ignore[return-value]

        try:
            if timeout is not None:
                runs = await asyncio.wait_for(_do_snapshot(), timeout=timeout)
            else:
                runs = await _do_snapshot()
        except asyncio.TimeoutError:
            return ActiveSessionQueryResult(
                query_status="timeout",
                verdict="unknown",
                engine=engine,
                checked_at=checked_at,
                count=0,
                sessions=[],
            )
        except Exception as e:  # pragma: no cover - defensive
            log.warning("active_sessions query failed: %s", e)
            return ActiveSessionQueryResult(
                query_status="error",
                verdict="unknown",
                engine=engine,
                checked_at=checked_at,
                count=0,
                sessions=[],
            )

        if any(not r.run_id or not r.session_key for r in runs):
            return _ok_result(engine, checked_at, runs, reason="incomplete")

        return _ok_result(engine, checked_at, runs)

    # ── Test/diagnostics helpers ──────────────────────────────────────────

    def all_runs(self) -> list[ActiveRun]:
        """Return every recorded run regardless of state (tests/diagnostics)."""
        return list(self._runs.values())


__all__ = [
    "ActiveRun",
    "ActiveRunRegistry",
    "ActiveSessionEntry",
    "ActiveSessionQueryResult",
    "RunState",
    "RUNNING_STATE",
]