"""Core-local ports consumed by the Caller identity domain service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.caller_identity.credential import (
    AuthContext,
    CallerToken,
)


@runtime_checkable
class CallerMcpSyncProtocol(Protocol):
    """Synchronize Caller-aware MCP identity to the external authority."""

    async def sync_mcp_identity_to_agent_principal(
        self,
        *,
        user_id: str,
        entity_id: str,
        bot_id: str,
        entity_type: str,
        engine_type: str,
        active_mcps: list[dict[str, Any]],
        identity_modes: Mapping[str, object],
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class CallerTokenProviderProtocol(Protocol):
    """Issue an opaque Caller execution credential."""

    def exchange(
        self,
        *,
        auth_context: AuthContext,
        iam_token: str,
        delegation_credential: str,
        task_metadata: Mapping[str, str],
    ) -> CallerToken: ...


@runtime_checkable
class CallerRuntimeUpdaterProtocol(Protocol):
    """Install the exchanged Caller credential on the runtime."""

    def update_caller_identity(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        caller_user_id: str,
        caller_token: CallerToken,
        agent_pass_token: str,
        agent_code: str,
        stage: str,
        publish_id: int | None,
    ) -> None: ...


__all__ = [
    "CallerMcpSyncProtocol",
    "CallerRuntimeUpdaterProtocol",
    "CallerTokenProviderProtocol",
]
