"""Close native online model sessions after application startup dependencies."""

from __future__ import annotations

from typing import Any

from app.application.ports.startup_provider import StartupProvider


class AsyncModelShutdownProvider:
    """Delegate startup lifecycle and close host-owned async model clients."""

    def __init__(self, delegate: StartupProvider, *, provider: Any) -> None:
        self._delegate = delegate
        self._provider = provider

    async def initialize(self) -> None:
        await self._delegate.initialize()

    async def shutdown(self) -> None:
        try:
            close = getattr(self._provider, "aclose", None)
            if close is not None:
                await close()
        finally:
            await self._delegate.shutdown()


__all__ = ["AsyncModelShutdownProvider"]
