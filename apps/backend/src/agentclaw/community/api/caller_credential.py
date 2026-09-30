"""Neutral in-process contract for Caller execution credentials.

Re-export only. The Protocol is defined in its owning core module
(``core/caller_identity/caller_credential_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.caller_identity.caller_credential_protocol import (
    CALLER_CHAT_TASK,
    CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_CREDENTIAL_UPSTREAM_FAILED,
    CALLER_OUTBOUND_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CALLER_TARGET_AMBIGUOUS,
    CALLER_TARGET_NOT_FOUND,
    AuthContext,
    CallerCredentialError,
    CallerRuntimeUpdater,
    CallerToken,
    CallerTokenProvider,
    UnavailableCallerTokenProvider,
)

__all__ = [
    "CALLER_CHAT_TASK",
    "CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE",
    "CALLER_CREDENTIAL_REQUEST_INVALID",
    "CALLER_CREDENTIAL_UPSTREAM_FAILED",
    "CALLER_OUTBOUND_INVALID",
    "CALLER_OUTBOUND_UPDATE_FAILED",
    "CALLER_TARGET_AMBIGUOUS",
    "CALLER_TARGET_NOT_FOUND",
    "AuthContext",
    "CallerCredentialError",
    "CallerRuntimeUpdater",
    "CallerToken",
    "CallerTokenProvider",
    "UnavailableCallerTokenProvider",
]
