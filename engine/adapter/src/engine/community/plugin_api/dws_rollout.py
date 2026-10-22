"""DWS-only runtime qualification, independent of interactive engines."""
from typing import Protocol


class HostingRollout(Protocol):
    async def allowed(self) -> bool: ...
    async def remember(self, payload: str, signature: str) -> None: ...
