"""Neutral API contract for Caller identity services."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.caller_identity.protocols import (
    CallerRuntimeUpdaterProtocol,
    CallerTokenProviderProtocol,
)
from agentclaw.community.core.caller_identity.contracts import (
    CALLER_IDENTITY_CAPABILITY,
    CallerCallTypeInvalidError,
    CallerContext,
    CallerIamTokenContext,
    CallerIdentityAmbiguousError,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
    CallerIdentityStage,
    CallerLockEpochError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
    McpCallType,
    McpCallTypeUpdateResult,
)
from agentclaw.community.plugin_api.passport import PassportPlugin


@runtime_checkable
class CallerIdentityServiceProtocol(Protocol):
    async def update_mcp_call_type(
        self,
        *,
        bot_id: str,
        server_code: str,
        call_type: McpCallType,
        actor_id: str,
        lock_epoch: int,
        entity_id: str | None = None,
    ) -> McpCallTypeUpdateResult: ...

    def get_context(
        self,
        *,
        bot_id: str,
        actor_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
        entity_id: str | None = None,
    ) -> CallerContext: ...

    def get_bot_call_type(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
        entity_id: str | None = None,
    ) -> McpCallType: ...

    def is_caller_bot(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
        entity_id: str | None = None,
    ) -> bool: ...

    def get_iam_token_context(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
        entity_id: str | None = None,
    ) -> CallerIamTokenContext: ...

    def exchange_caller_identity(
        self,
        *,
        iam_token: str,
        caller_user_id: str,
        bot_id: str,
        owner_user_id: str,
        passport: PassportPlugin,
        token_provider: CallerTokenProviderProtocol,
        runtime_updater: CallerRuntimeUpdaterProtocol,
        stage: str,
        publish_id: int | None,
        entity_id: str | None = None,
        binding_id: int | None = None,
    ) -> None: ...


__all__ = [
    "CALLER_IDENTITY_CAPABILITY",
    "CallerCallTypeInvalidError",
    "CallerContext",
    "CallerIamTokenContext",
    "CallerIdentityAmbiguousError",
    "CallerIdentityNotFoundError",
    "CallerIdentityPermissionError",
    "CallerIdentityReadOnlyError",
    "CallerIdentityServiceProtocol",
    "CallerIdentityStage",
    "CallerLockEpochError",
    "CallerMcpNotFoundError",
    "CallerMcpSyncError",
    "McpCallType",
    "McpCallTypeUpdateResult",
]
