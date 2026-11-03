"""DWS-only runtime qualification, independent of interactive engines."""
from typing import Protocol


class HostingRollout(Protocol):
    """False revokes eligibility; RolloutUnavailable fences work until retry."""

    async def allowed(self) -> bool: ...
    async def remember(self, payload: str, signature: str) -> None: ...
