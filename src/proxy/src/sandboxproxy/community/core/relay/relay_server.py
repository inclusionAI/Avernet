"""WebSocket relay core — lazy mng-first session pairing + bidirectional forward."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from sandboxproxy.community.logger import get_logger
from sandboxproxy.community.spi import RelayApiClient

logger = get_logger("relay")


@dataclass
class _WaitingSession:
    session_id: str
    created_at: float = field(default_factory=time.monotonic)
    mng_ready: asyncio.Future[Any] = field(default_factory=asyncio.Future)


class RelayRegistry:
    """In-memory pairing of mng connections waiting for session ids."""

    def __init__(self, *, wait_timeout: float = 30.0) -> None:
        self._wait_timeout = wait_timeout
        self._waiting: dict[str, _WaitingSession] = {}

    def register_mng(self, session_id: str) -> asyncio.Future[Any]:
        """Create a waiting entry for a mng; return its ready future."""
        entry = _WaitingSession(session_id=session_id)
        self._waiting[session_id] = entry
        return entry.mng_ready

    def signal_mng_ready(self, session_id: str, websocket: Any) -> None:
        """Resolve the mng ready future with the connected websocket."""
        entry = self._waiting.get(session_id)
        if entry is not None and not entry.mng_ready.done():
            entry.mng_ready.set_result(websocket)

    def connect_client(self, session_id: str) -> asyncio.Future[Any] | None:
        """Client side: return the ready future if a mng is waiting."""
        entry = self._waiting.pop(session_id, None)
        return entry.mng_ready if entry else None

    def cleanup_expired(self) -> list[str]:
        now = time.monotonic()
        expired = [
            sid
            for sid, entry in self._waiting.items()
            if now - entry.created_at > self._wait_timeout
        ]
        for sid in expired:
            self._waiting.pop(sid, None)
        return expired

    def all_sessions(self) -> list[str]:
        return list(self._waiting.keys())


class RelayServer:
    """Coordinates relay sessions against the upstream BaaS relay API."""

    def __init__(
        self,
        relay_client: RelayApiClient,
        *,
        wait_timeout: float = 30.0,
        cleanup_interval: float = 5.0,
    ) -> None:
        self._relay = relay_client
        self._registry = RelayRegistry(wait_timeout=wait_timeout)
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        for sid in self._registry.all_sessions():
            await self._relay.mark_route_closed(sid)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self._cleanup_interval)
            for sid in self._registry.cleanup_expired():
                await self._relay.mark_route_closed(sid)
                logger.info("relay session %s timed out, route closed", sid)

    async def register_mng(self, session_id: str) -> bool:
        """Register a waiting mng; write active route. False on route failure."""
        if not await self._relay.upsert_route_active(session_id):
            return False
        self._registry.register_mng(session_id)
        return True

    def signal_mng_ready(self, session_id: str, websocket: Any) -> None:
        self._registry.signal_mng_ready(session_id, websocket)

    async def wait_for_client(self, session_id: str) -> asyncio.Future[Any] | None:
        return self._registry.connect_client(session_id)

    async def connect_client(self, session_id: str) -> asyncio.Future[Any] | None:
        return self._registry.connect_client(session_id)

    async def close_session(self, session_id: str) -> None:
        await self._relay.mark_route_closed(session_id)


async def bidirectional_forward(
    ws_a: Any,
    ws_b: Any,
) -> None:
    """Forward frames bidirectionally; return when either side closes."""

    async def pump(src: Any, dst: Any) -> None:
        try:
            while True:
                message = await src.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message["type"] == "websocket.receive":
                    await dst.send(message)
        except Exception as exc:  # pragma: no cover - connection teardown
            logger.debug("pump terminated: %s", exc)

    task_a = asyncio.create_task(pump(ws_a, ws_b))
    task_b = asyncio.create_task(pump(ws_b, ws_a))
    done, pending = await asyncio.wait(
        (task_a, task_b), return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
