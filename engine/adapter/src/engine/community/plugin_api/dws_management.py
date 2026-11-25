"""Management persistence, called only by the owner management service."""

from typing import Protocol
from engine.community.kernel.dws_hosting import Binding
from engine.community.kernel.dws_management import SettingsUpdate


class ManagementProofVerifier(Protocol):
    """Verify exact command bytes with the trusted Backend; never fail open."""

    async def verify(self, payload: str, signature: str) -> bool: ...

    async def runtime_eligible(self, payload: str, signature: str) -> bool:
        """Raise RolloutUnavailable for transport/response failures, not denial."""
        ...


class HostingManagementStore(Protocol):
    def get(self, binding: Binding) -> dict: ...
    def save(self, binding: Binding, update: SettingsUpdate) -> dict: ...
    def conversations(self, binding: Binding, *, offset: int, limit: int) -> dict: ...
    def consume_nonce(self, nonce: str, now_ms: int) -> None: ...
