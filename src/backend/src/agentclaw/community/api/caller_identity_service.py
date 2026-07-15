"""Neutral API contract for Caller identity services."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.caller_identity.contracts import (
    CALLER_IDENTITY_CAPABILITY,
    CallerCallTypeInvalidError,
    CallerContext,
    CallerIamTokenContext,
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
    ) -> McpCallTypeUpdateResult: ...

    def get_context(
        self,
        *,
        bot_id: str,
        actor_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> CallerContext: ...

    def get_bot_call_type(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> McpCallType: ...

    def is_caller_bot(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> bool: ...

    def get_iam_token_context(
        self,
        bot_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None = None,
    ) -> CallerIamTokenContext: ...


__all__ = [
    "CALLER_IDENTITY_CAPABILITY",
    "CallerCallTypeInvalidError",
    "CallerContext",
    "CallerIamTokenContext",
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
