"""Service API contract for scoped MCP Header and URL configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from agentclaw.community.core.bot_management.addressing import ResolvedBotContext


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


class BotMCPConfigPatch(TypedDict, total=False):
    headers: dict[str, str | None]
    url: str | None


@dataclass(frozen=True)
class BotMCPConfigUpdate:
    config: dict[str, Any]
    changed: bool
    projection_status: Literal["CONVERGED", "SKIPPED", "DEGRADED"]
    projection_reason: str | None = None


@runtime_checkable
class MCPScopedConfigServiceProtocol(Protocol):
    """Read and replace account rules or patch one Bot's MCP connection overrides."""

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

    def read_bot(
        self, *, bot: ResolvedBotContext, server_code: str
    ) -> dict[str, Any]: ...

    async def patch_bot(
        self, *, bot: ResolvedBotContext, server_code: str, patch: BotMCPConfigPatch
    ) -> BotMCPConfigUpdate: ...
