from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.plugin_api.auth_gate.models import VerifyResult


@runtime_checkable
class AuthGateService(Protocol):
    async def verify(self, token: str, content: str, session_id: str) -> VerifyResult: ...
    async def get_switch(self) -> bool: ...
    async def set_switch(self, enabled: bool) -> None: ...
