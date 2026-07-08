"""Sandbox device router protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class SandboxDeviceInfo:
    """Sandbox device summary info."""

    table_id: int
    table_type: str
    sandbox_id: str | None = None
    ttl_expiration_time: str | None = None
    ttl_expiration_timestamp: int | None = None
    refresh_fail_count: int = 0
    status: str = ""


@dataclass
class PaginatedResult:
    """Paginated query result."""

    total: int
    page: int
    page_size: int
    items: list[SandboxDeviceInfo] = field(default_factory=list)


@dataclass
class WarnResult:
    """Warning operation result."""

    table_id: int
    table_type: str
    action: str  # "STOPPED" | "RESET" | "INCREMENT"
    refresh_fail_count: int = 0


@dataclass
class RenewTtlResult:
    """TTL renewal operation result."""

    table_id: int
    table_type: str
    device_id: str
    success: bool
    old_expiration_time: str | None = None
    new_expiration_time: str | None = None
    refresh_fail_count: int = 0
    error: str | None = None


@runtime_checkable
class SandboxDeviceRouter(Protocol):
    """Protocol for sandbox device router — routes sandbox operations by table_type."""

    def query_active_sandboxes(
        self,
        *,
        env: str = "prod",
        table_type: str,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult: ...

    async def warn_device(self, *, table_id: int) -> WarnResult: ...

    async def renew_ttl(self, *, table_id: int) -> RenewTtlResult: ...
