"""Stub relay client — in-memory relay-session state for single-box."""

from __future__ import annotations

from typing import Any


class StubRelayClient:
    """No-op relay client: never contacts BaaS, always reports success."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._routes: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        self._routes.clear()

    async def upsert_route_active(self, session_id: str) -> bool:
        self._routes[session_id] = {"status": "active", "session_id": session_id}
        return True

    async def get_route_info(self, session_id: str) -> dict[str, Any] | None:
        return self._routes.get(session_id)

    async def mark_route_closed(self, session_id: str) -> bool:
        self._routes[session_id] = {"status": "closed", "session_id": session_id}
        return True
