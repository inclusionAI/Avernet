"""Service API Protocol for cron relay (proxy + listing)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CronRelayServiceProtocol(Protocol):
    """Service API for cron listing and request forwarding."""

    async def list_all_crons(self, *args: Any, **kwargs: Any) -> Any: ...

    async def list_running_crons(self, *args: Any, **kwargs: Any) -> Any: ...

    async def find_auto_initiate_and_run(self, *args: Any, **kwargs: Any) -> Any: ...

    async def run_single_auto_initiate(self, *args: Any, **kwargs: Any) -> Any: ...

    async def forward_request(self, *args: Any, **kwargs: Any) -> Any: ...
