"""Neutral in-process contract for Caller execution credentials.

This module intentionally contains no HTTP, secret-store, or corp imports.
Only a production adapter may implement :class:`CallerTokenProvider`; callers
receive an opaque, immutable token and stable failure codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from agentclaw.community.core.caller_identity.credential import (
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
    CallerToken,
)


@runtime_checkable
class CallerTokenProvider(Protocol):
    """Exchange authenticated Caller context for an execution credential."""

    def exchange(
        self,
        *,
        auth_context: AuthContext,
        iam_token: str,
        delegation_credential: str,
        task_metadata: Mapping[str, str],
    ) -> CallerToken:
        """Issue one non-retried Caller execution credential."""
        ...


@runtime_checkable
class CallerRuntimeUpdater(Protocol):
    """Install the current Caller identity on the exact runtime device."""

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
    ) -> None:
        """Replace the device's complete outbound rule with Caller overlay."""
        ...


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
]
