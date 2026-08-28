"""Trusted inputs and results for private runtime binding resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuntimeBindingSource(StrEnum):
    PERSONAL = "personal"
    SERVICE_DRAFT = "service_draft"
    SERVICE_VERIFY = "service_verify"
    SERVICE_ONLINE = "service_online"
    CALLER_INSTANCE = "caller_instance"


class RuntimeBindingTarget(StrEnum):
    """Explicit runtime family requested by a trusted server-side caller."""

    AUTO = "auto"
    CALLER_SERVICE = "caller_service"
    CALLER_INSTANCE = "caller_instance"


@dataclass(frozen=True, slots=True)
class RuntimeBindingRequest:
    """Trusted request context used to find one existing binding."""

    bot_id: str
    owner_id: str
    actor_user_id: str
    stage: str = "draft"
    allow_initializing_caller_binding_id: int | None = None
    environment: str | None = None
    target: RuntimeBindingTarget = RuntimeBindingTarget.AUTO


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeBinding:
    """A private binding selected from the bot's existing runtime state."""

    binding_id: int
    source: RuntimeBindingSource


__all__ = [
    "ResolvedRuntimeBinding",
    "RuntimeBindingRequest",
    "RuntimeBindingSource",
    "RuntimeBindingTarget",
]
