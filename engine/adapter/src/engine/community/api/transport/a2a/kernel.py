"""A2A transport kernel — run lifecycle, subscription, D14 idempotency.

Design: docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md §8
Spike reference: spike/a2a/.harness/src/kernel.py (55 tests green)

Invariants:
  1. last_seq independent of event buffer (cache_limit clears buffer, not seq)
  2. cache_limit (count/bytes/total budget) marks explicit replay refusal;
     delivery continues
  3. subscribe(): lock-internal snapshot+registration (no loss window)
  4. slow subscriber: kicked on QueueFull with termination signal
  5. complete_run(): set state → publish terminal → finish
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any

from engine.community.api.transport.a2a import wire

MAX_RUNS = 16
MAX_EVENTS_PER_RUN = 20_000
MAX_BYTES_PER_RUN = 16 * 1024 * 1024
TOTAL_BUDGET_BYTES = 64 * 1024 * 1024
SUBSCRIBER_QUEUE_SIZE = 256
TERMINAL_TTL_SECONDS = 180
# inject 操作级去重表上限与 TTL（超过上限先按 TTL 清理过期项）
_INJECT_DEDUP_MAX = 10_000
_INJECT_DEDUP_TTL_SECONDS = 1_800
# R13-2 inject 生命周期状态
_INJECT_IN_PROGRESS = "in_progress"
_INJECT_ACCEPTED = "accepted"
_INJECT_UNKNOWN = "unknown"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class UnknownOutcomeSentinel:
    def __repr__(self) -> str:
        return "UNKNOWN_OUTCOME"


class KeyConflictError(Exception):
    pass


class _InvalidParamError(ValueError):
    """Boundary type violation — maps to JSON-RPC -32602."""


class _Run:
    __slots__ = ("run_id", "context_id", "state", "events", "bytes", "last_seq",
                 "saw_delta", "cache_limit", "subscribers", "lock",
                 "replay_expires_at", "idempotency_key", "request_digest",
                 "latest_text")

    def __init__(self, run_id: str, context_id: str):
        self.run_id = run_id
        self.context_id = context_id
        self.state = "TASK_STATE_SUBMITTED"
        self.events: list[dict[str, Any]] = []
        self.bytes = 0
        self.last_seq = 0
        self.saw_delta = False
        self.cache_limit = False
        self.subscribers: list[asyncio.Queue] = []
        self.lock = asyncio.Lock()
        self.replay_expires_at: float | None = None
        self.idempotency_key: str | None = None
        self.request_digest: str | None = None
        # R12-7: content snapshot maintained independently of the replay
        # buffer — cache_limit clears events but GetTask still answers with
        # the latest generated text.
        self.latest_text: str = ""


class A2aKernel:
    UNKNOWN_OUTCOME = UnknownOutcomeSentinel()

    def __init__(self) -> None:
        self.runs: dict[str, _Run] = {}
        self._idem: dict[tuple[str, str], str] = {}
        self._inject_dedup: dict[tuple[str, str], tuple[str, float]] = {}
        self._context_subs: dict[str, list[asyncio.Queue]] = {}
        self._total_bytes = 0
        self._global_lock = asyncio.Lock()

    # ── D14 atomic entry ──
    async def open_run(self, context_id: str | None, idempotency_key: str,
                       mode: str, request_digest: str | None = None
                       ) -> tuple[str, bool] | UnknownOutcomeSentinel:
        # R12-8: unhashable wire values (list/dict messageId or contextId)
        # would blow up as TypeError deep inside the idem dict — reject at
        # the boundary with the protocol error instead.
        if not isinstance(context_id, str):
            raise ValueError("contextId is required")
        if not isinstance(idempotency_key, str):
            raise _InvalidParamError("messageId must be a string")
        if not context_id or not idempotency_key:
            raise ValueError("contextId is required")
        async with self._global_lock:
            existing = self._idem.get((context_id, idempotency_key))
            if existing is not None and existing in self.runs:
                r = self.runs[existing]
                if r.request_digest != request_digest:
                    raise KeyConflictError((context_id, idempotency_key))
                return existing, False
            if mode == "retry_only":
                return self.UNKNOWN_OUTCOME
            if len(self.runs) >= MAX_RUNS:
                # D7/v3 语义对齐（openclaw_resume 硬拒）：无可驱逐的终态
                # run 时显式拒绝，不做无声突破上限。
                self._evict_oldest_finished()
                if len(self.runs) >= MAX_RUNS:
                    raise RuntimeError(
                        f"run capacity exhausted ({MAX_RUNS} active runs)")
            rid = uuid.uuid4().hex[:12]
            r = _Run(rid, context_id)
            r.idempotency_key = idempotency_key
            r.request_digest = request_digest
            self.runs[rid] = r
            self._idem[(context_id, idempotency_key)] = rid

        await self._publish(rid, wire.wire_task_event(rid, context_id,
                                                      r.state))
        return rid, True

    async def send_message(self, context_id: str, message_text: str,
                           idempotency_key: str, mode: str = "first"
                           ) -> dict[str, Any] | UnknownOutcomeSentinel:
        digest = _digest(message_text)
        out = await self.open_run(context_id, idempotency_key, mode, digest)
        if out is self.UNKNOWN_OUTCOME:
            return out
        rid, created = out
        r = self.runs.get(rid)
        if r is None:
            return self.UNKNOWN_OUTCOME
        return {"task": {"id": r.run_id, "contextId": r.context_id,
                         "status": {"state": r.state},
                         "metadata": {"created": created}}}

    # ── completion ──
    async def complete_run(self, rid: str,
                           state: str = "TASK_STATE_COMPLETED") -> None:
        r = self.runs[rid]
        r.state = state
        await self._publish(rid, wire.wire_status_update(
            rid, r.context_id, state, seq=r.last_seq))
        r.replay_expires_at = time.monotonic() + TERMINAL_TTL_SECONDS
        marker = wire.wire_terminal_marker(rid, r.context_id, state,
                                           r.last_seq)
        for q in list(r.subscribers):
            self._deliver_terminal(r, q, marker)
        asyncio.get_running_loop().call_later(TERMINAL_TTL_SECONDS,
                                              self._evict, rid)

    def _deliver_terminal(self, r: _Run, q: asyncio.Queue,
                          item: dict[str, Any] | None) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            if q in r.subscribers:
                r.subscribers.remove(q)
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def _evict(self, rid: str) -> None:
        r = self.runs.pop(rid, None)
        if r:
            self._total_bytes -= r.bytes
            self._idem.pop((r.context_id, r.idempotency_key), None)
            for q in list(r.subscribers):
                self._deliver_terminal(r, q, None)

    def _evict_oldest_finished(self) -> None:
        for rid, r in sorted(self.runs.items(),
                             key=lambda kv: kv[1].replay_expires_at or 0):
            if r.replay_expires_at is not None:
                self._evict(rid)
                return

    # ── publish ──
    async def _publish(self, rid: str, event: dict[str, Any]) -> None:
        r = self.runs[rid]
        async with r.lock:
            r.last_seq += 1
            event["seq"] = r.last_seq
            event_size = len(str(event))

            if not r.cache_limit:
                fits = (len(r.events) < MAX_EVENTS_PER_RUN
                        and r.bytes + event_size <= MAX_BYTES_PER_RUN
                        and self._total_bytes + event_size <= TOTAL_BUDGET_BYTES)
                if fits:
                    r.events.append(event)
                    r.bytes += event_size
                    self._total_bytes += event_size
                else:
                    self._total_bytes -= r.bytes
                    r.events = []
                    r.bytes = 0
                    r.cache_limit = True

            for q in list(r.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    r.subscribers.remove(q)
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

    async def publish_delta(self, rid: str, text: str) -> None:
        r = self.runs[rid]
        first = not r.saw_delta
        r.saw_delta = True
        r.latest_text += text          # 增量累积进内容快照（R12-7）
        artifact_id = f"text-{rid}"
        await self._publish(rid, wire.wire_artifact_update(
            rid, r.context_id, 0, artifact_id, text,
            append=not first, last_chunk=False))

    async def publish_final(self, rid: str, text: str) -> None:
        r = self.runs[rid]
        r.latest_text = text           # 终稿全量替换快照（R12-7）
        await self._publish(rid, wire.wire_artifact_update(
            rid, r.context_id, 0, f"text-{rid}", text,
            append=False, last_chunk=True))

    def publish_engine_inject(self, context_id: str, text: str,
                              inject_id: str) -> None:
        """R12-3: engine-callback inject frames (runId=inject-*) fan out to
        context subscribers only — they never touch the current run's
        artifact stream or lifecycle (ws_server keeps the same exception)."""
        ev = wire.wire_inject_message(context_id, text, inject_id)
        for q in list(self._context_subs.get(context_id, [])):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    async def publish_status(self, rid: str, state: str) -> None:
        r = self.runs[rid]
        r.state = state
        await self._publish(rid, wire.wire_status_update(
            rid, r.context_id, state, seq=0))

    # ── subscribe / replay ──
    async def subscribe(self, rid: str, since_seq: int | None = None
                        ) -> tuple[list[dict[str, Any]], asyncio.Queue]:
        r = self.runs.get(rid)
        if r is None:
            raise LookupError(rid)

        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        async with r.lock:
            backlog = ([] if since_seq is None
                       else [e for e in r.events if e["seq"] > since_seq])
            r.subscribers.append(q)
            if since_seq is None:
                q.put_nowait(wire.wire_task_event(rid, r.context_id,
                                                  r.state))
        if r.state in wire.TERMINAL_STATES:
            q.put_nowait(wire.wire_terminal_marker(rid, r.context_id,
                                                   r.state, r.last_seq))
        return backlog, q

    async def replay(self, rid: str, since_seq: int
                     ) -> tuple[list[dict[str, Any]], asyncio.Queue] | None:
        r = self.runs.get(rid)
        if r is None or r.cache_limit:
            return None
        if (r.replay_expires_at is not None
                and time.monotonic() > r.replay_expires_at):
            return None
        return await self.subscribe(rid, since_seq=since_seq)

    def unsubscribe(self, rid: str, q: asyncio.Queue) -> None:
        r = self.runs.get(rid)
        if r and q in r.subscribers:
            r.subscribers.remove(q)

    # ── inject lifecycle (R13-2: dedup state and engine write are coupled) ──
    #
    # States per (context, inject_id) key:
    #   in_progress — reserved, engine write in flight
    #   accepted    — engine write confirmed; event broadcast happened
    #   unknown     — timeout/ambiguous outcome; key retained (engine may
    #                 have written — never blindly delete and retry)
    #
    # Broadcast happens exactly at commit (after engine confirmation).
    # Explicit engine rejection rolls the key back — retry is allowed.

    async def inject_reserve(self, context_id: str, inject_id: str) -> str:
        """Returns 'in_progress' for a fresh reservation, or the
        'duplicate_<state>' status of an existing key."""
        key = (context_id, inject_id)
        now = time.monotonic()
        # M1: TTL prune keeps the dedup table bounded on long-lived
        # processes (records expire rather than accumulate forever).
        if len(self._inject_dedup) > _INJECT_DEDUP_MAX:
            expired = [k for k, (_, ts) in self._inject_dedup.items()
                       if now - ts > _INJECT_DEDUP_TTL_SECONDS]
            for k in expired:
                self._inject_dedup.pop(k, None)
        existing = self._inject_dedup.get(key)
        if existing is not None:
            return f"duplicate_{existing[0]}"
        self._inject_dedup[key] = (_INJECT_IN_PROGRESS, now)
        return "in_progress"

    def inject_commit(self, context_id: str, text: str,
                      inject_id: str) -> bool:
        """Mark accepted and broadcast to context subscribers — only after
        the engine write confirmed. Returns False when the key is gone."""
        key = (context_id, inject_id)
        existing = self._inject_dedup.get(key)
        if existing is None:
            return False
        self._inject_dedup[key] = (_INJECT_ACCEPTED, existing[1])
        ev = wire.wire_inject_message(context_id, text, inject_id)
        for q in list(self._context_subs.get(context_id, [])):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return True

    def inject_rollback(self, context_id: str,
                        inject_id: str) -> bool:
        """Explicit engine rejection: drop the key so a same-key retry
        re-attempts the write. Never broadcast."""
        key = (context_id, inject_id)
        existing = self._inject_dedup.pop(key, None)
        return existing is not None

    def inject_unknown(self, context_id: str, inject_id: str) -> None:
        """Ambiguous outcome (timeout): retain the key as unknown — the
        engine may already have written; a same-key retry must not
        blindly re-execute. A new operation uses a new messageId."""
        key = (context_id, inject_id)
        existing = self._inject_dedup.get(key)
        if existing is not None:
            self._inject_dedup[key] = (_INJECT_UNKNOWN, existing[1])

    def subscribe_context(self, context_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._context_subs.setdefault(context_id, []).append(q)
        return q

    def unsubscribe_context(self, context_id: str,
                            q: asyncio.Queue) -> None:
        subs = self._context_subs.get(context_id, [])
        if q in subs:
            subs.remove(q)

    def get_run(self, rid: str) -> _Run | None:
        return self.runs.get(rid)
