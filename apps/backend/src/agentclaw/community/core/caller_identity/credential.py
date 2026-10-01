"""Behavior-free Caller credential values shared by core and API ports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType


CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE = "CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE"
CALLER_CREDENTIAL_REQUEST_INVALID = "CALLER_CREDENTIAL_REQUEST_INVALID"
CALLER_CREDENTIAL_UPSTREAM_FAILED = "CALLER_CREDENTIAL_UPSTREAM_FAILED"
CALLER_OUTBOUND_INVALID = "CALLER_OUTBOUND_INVALID"
CALLER_OUTBOUND_UPDATE_FAILED = "CALLER_OUTBOUND_UPDATE_FAILED"
CALLER_TARGET_AMBIGUOUS = "CALLER_TARGET_AMBIGUOUS"
CALLER_TARGET_NOT_FOUND = "CALLER_TARGET_NOT_FOUND"

CALLER_CHAT_TASK: Mapping[str, str] = MappingProxyType(
    {
        "taskCode": "teamclaw_chat",
        "taskName": "TeamClaw Chat",
        "taskType": "CALLER",
        "triggerSource": "TEAMCLAW_CHAT",
    }
)


@dataclass(frozen=True)
class AuthContext:
    """Authenticated actor supplied by the trusted application adapter."""

    user_id: str


@dataclass(frozen=True)
class CallerToken:
    """Opaque Caller execution credential kept only in process memory."""

    access_token: str
    subject_user_id: str
    expires_at: datetime
    fingerprint: str


class CallerCredentialError(RuntimeError):
    """Caller credential failure exposing only a stable internal code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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
    "CallerToken",
]
