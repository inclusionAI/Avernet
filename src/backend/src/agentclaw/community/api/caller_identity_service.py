"""Neutral API contract for Caller identity services.

Re-export only. The Protocol is defined in its owning core module
(``core/caller_identity/caller_identity_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.caller_identity.caller_identity_service_protocol import (
    CALLER_IDENTITY_CAPABILITY,
    CallerCallTypeInvalidError,
    CallerCliNotFoundError,
    CallerCliSyncError,
    CallerContext,
    CallerIamTokenContext,
    CallerIdentityAmbiguousError,
    CallerIdentityIrreversibleError,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
    CallerIdentityServiceProtocol,
    CallerIdentityStage,
    CallerLockEpochError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
    CallerRuntimeUpdaterProtocol,
    CallerToken,
    CallerTokenProviderProtocol,
    McpCallType,
    McpCallTypeUpdateResult,
    CliCallTypeUpdateResult,
)

__all__ = [
    "CALLER_IDENTITY_CAPABILITY",
    "CallerCallTypeInvalidError",
    "CallerCliNotFoundError",
    "CallerCliSyncError",
    "CallerContext",
    "CallerIamTokenContext",
    "CallerIdentityAmbiguousError",
    "CallerIdentityIrreversibleError",
    "CallerIdentityNotFoundError",
    "CallerIdentityPermissionError",
    "CallerIdentityReadOnlyError",
    "CallerIdentityServiceProtocol",
    "CallerIdentityStage",
    "CallerLockEpochError",
    "CallerMcpNotFoundError",
    "CallerMcpSyncError",
    "CliCallTypeUpdateResult",
    "CallerRuntimeUpdaterProtocol",
    "CallerToken",
    "CallerTokenProviderProtocol",
    "McpCallType",
    "McpCallTypeUpdateResult",
]
