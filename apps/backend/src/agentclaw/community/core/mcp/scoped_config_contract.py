"""Service API contract for scoped MCP Header and URL configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class HeaderGroup:
    key: str
    value: str
    bots: tuple[str, ...]


@dataclass(frozen=True)
class URLRule:
    url: str
    bots: tuple[str, ...]


@dataclass(frozen=True)
class ScopedMCPConfig:
    server_code: str
    endpoint_env: str
    transport_protocol: str | None
    params: tuple[HeaderGroup, ...]
    url_rules: tuple[URLRule, ...] = ()
    sync_results: tuple[dict[str, Any], ...] | None = None
    sync_summary: dict[str, int] | None = None


@runtime_checkable
class MCPScopedConfigServiceProtocol(Protocol):
    """Read and replace a user's scoped MCP connection rules."""

    def read(self, *, user_id: str, server_code: str) -> ScopedMCPConfig: ...

    async def replace(
        self,
        *,
        user_id: str,
        server_code: str,
        endpoint_env: str,
        transport_protocol: str | None,
        params: tuple[HeaderGroup, ...],
        url_rules: tuple[URLRule, ...] | None = None,
    ) -> ScopedMCPConfig: ...
