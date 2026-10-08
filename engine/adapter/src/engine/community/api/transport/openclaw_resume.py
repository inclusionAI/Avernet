"""Short-lived, process-local replay channels for OpenClaw chat streams."""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

from engine.community.kernel.frames import EventFrame


MAX_RUNS = 16
MAX_EVENTS_PER_RUN = 20_000
MAX_BYTES_PER_RUN = 16 * 1024 * 1024
MAX_BYTES_TOTAL = 64 * 1024 * 1024
SUBSCRIBER_QUEUE_SIZE = 256
TERMINAL_TTL_SECONDS = 180


@dataclass
class _Subscriber:
    queue: asyncio.Queue[str | None]
    task: asyncio.Task[None]


class OpenClawResumeRun:
    def __init__(
        self,
        registry: OpenClawResumeRegistry,
        *,
        session_key: str,
        run_id: str,
        ticket_digest: str,
    ) -> None:
        self._registry = registry
        self.session_key = session_key
        self.run_id = run_id
        self.ticket_digest = ticket_digest
        self.state = "starting"
        self.reason: str | None = None
        self.finished_at: float | None = None
        self.task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._events: list[str] = []
        self._bytes = 0
        self._seq = 0
        self._subscribers: dict[str, _Subscriber] = {}

    @property
    def resumable(self) -> bool:
        return self.reason is None

    @property
    def buffered_bytes(self) -> int:
        return self._bytes

    def summary(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "state": self.state,
            "resumable": self.resumable,
            "reason": self.reason,
            "lastResumeSeq": self._seq,
            "bufferedEvents": len(self._events),
        }

    async def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._seq += 1
            outbound = dict(payload)
            outbound["resumeSeq"] = self._seq
            frame = EventFrame(
                event=event_name,
                payload=outbound,
                seq=outbound.get("seq"),
            ).to_json()
            size = len(frame.encode("utf-8"))
            if self.resumable:
                if (
                    len(self._events) >= MAX_EVENTS_PER_RUN
                    or self._bytes + size > MAX_BYTES_PER_RUN
                    or self._registry.buffered_bytes + size > MAX_BYTES_TOTAL
                ):
                    self.reason = "CACHE_LIMIT"
                    self._registry.buffered_bytes -= self._bytes
                    self._bytes = 0
                    self._events.clear()
                else:
                    self._events.append(frame)
                    self._bytes += size
                    self._registry.buffered_bytes += size

            for conn_id, subscriber in list(self._subscribers.items()):
                try:
                    subscriber.queue.put_nowait(frame)
                except asyncio.QueueFull:
                    self._subscribers.pop(conn_id, None)
                    subscriber.task.cancel()

            state = outbound.get("state")
            run_id = outbound.get("runId")
            if state in ("final", "error", "aborted") and not (
                isinstance(run_id, str) and run_id.startswith("inject-")
            ):
                self.state = state

    async def attach(self, conn_id: str, websocket: Any) -> dict[str, Any] | None:
        async with self._lock:
            if not self.resumable:
                return None
            old = self._subscribers.pop(conn_id, None)
            if old is not None:
                old.task.cancel()
            replay = list(self._events)
            queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
            if self.finished_at is not None:
                queue.put_nowait(None)
            subscriber = _Subscriber(
                queue=queue,
                task=asyncio.create_task(
                    self._send_to_subscriber(conn_id, websocket, replay, queue)
                ),
            )
            self._subscribers[conn_id] = subscriber
            return {
                "runId": self.run_id,
                "state": self.state,
                "replayCount": len(replay),
                "lastResumeSeq": self._seq,
            }

    async def detach(self, conn_id: str) -> None:
        async with self._lock:
            subscriber = self._subscribers.pop(conn_id, None)
            if subscriber is not None:
                subscriber.task.cancel()

    async def finish(self) -> None:
        async with self._lock:
            if self.state not in ("final", "error", "aborted"):
                self.state = "error"
            self.finished_at = time.monotonic()
            for subscriber in self._subscribers.values():
                try:
                    subscriber.queue.put_nowait(None)
                except asyncio.QueueFull:
                    subscriber.task.cancel()
        asyncio.get_running_loop().call_later(
            TERMINAL_TTL_SECONDS, self._registry.discard, self
        )

    async def _send_to_subscriber(
        self,
        conn_id: str,
        websocket: Any,
        replay: list[str],
        queue: asyncio.Queue[str | None],
    ) -> None:
        try:
            for frame in replay:
                await websocket.send_text(frame)
            while (frame := await queue.get()) is not None:
                await websocket.send_text(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            subscriber = self._subscribers.get(conn_id)
            if subscriber is not None and subscriber.queue is queue:
                self._subscribers.pop(conn_id, None)


class OpenClawResumeRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, OpenClawResumeRun] = {}
        self.buffered_bytes = 0

    def create(self, *, session_key: str, run_id: str) -> tuple[OpenClawResumeRun, str] | None:
        self.prune()
        if len(self._runs) >= MAX_RUNS:
            completed = [
                run for run in self._runs.values() if run.finished_at is not None
            ]
            if not completed:
                return None
            self.discard(min(completed, key=lambda run: run.finished_at or 0))
        # COSEC: the unpredictable ticket is the read credential; retain only its digest.
        ticket = secrets.token_urlsafe(32)
        digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()
        run = OpenClawResumeRun(
            self,
            session_key=session_key,
            run_id=run_id,
            ticket_digest=digest,
        )
        self._runs[digest] = run
        return run, ticket

    def lookup(self, *, session_key: Any, ticket: Any) -> OpenClawResumeRun | None:
        self.prune()
        if not isinstance(session_key, str) or not session_key:
            return None
        if not isinstance(ticket, str) or not 32 <= len(ticket) <= 128:
            return None
        digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
        run = self._runs.get(digest)
        if run is None or run.session_key != session_key:
            return None
        return run

    def prune(self) -> None:
        now = time.monotonic()
        for run in list(self._runs.values()):
            if run.finished_at is None or now - run.finished_at < TERMINAL_TTL_SECONDS:
                continue
            self.discard(run)

    def discard(self, run: OpenClawResumeRun) -> None:
        if self._runs.pop(run.ticket_digest, None) is run:
            self.buffered_bytes -= run.buffered_bytes
            run._events.clear()
            run._bytes = 0
            for subscriber in run._subscribers.values():
                subscriber.task.cancel()
            run._subscribers.clear()

    async def detach(self, conn_id: str) -> None:
        for run in list(self._runs.values()):
            await run.detach(conn_id)
