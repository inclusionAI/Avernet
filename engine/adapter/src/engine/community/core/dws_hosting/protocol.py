"""DWS lifecycle Service API; separate from HTTP and automatic activation."""

from typing import Protocol

from engine.community.kernel.dws_runtime import HostingIssue, Resolution, RuntimeStatus


class HostingRuntimeService(Protocol):
    async def run(self) -> None:
        """Start only a previously enabled binding; cancel to stop and close intake."""
        ...

    async def status(self, actor_id: str) -> RuntimeStatus: ...

    async def issues(
        self, actor_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[HostingIssue, ...]: ...

    async def resolve_manual(self, actor_id: str, resolution: Resolution) -> None:
        """While stopped, verify uncertain outcomes and hand the conversation to owner."""
        ...
