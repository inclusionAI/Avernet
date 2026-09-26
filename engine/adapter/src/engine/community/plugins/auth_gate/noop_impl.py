from __future__ import annotations

import logging

from engine.community.plugin_api.auth_gate.models import VerifyResult

log = logging.getLogger("engine.auth_gate")


class NoopAuthGateService:
    def __init__(self) -> None:
        self._enabled = False

    async def verify(self, token: str, content: str, session_id: str) -> VerifyResult:
        if self._enabled:
            log.warning("Community AuthGate is enabled but uses allow-all no-op")
        return VerifyResult(allowed=True)

    async def get_switch(self) -> bool:
        return self._enabled

    async def set_switch(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
