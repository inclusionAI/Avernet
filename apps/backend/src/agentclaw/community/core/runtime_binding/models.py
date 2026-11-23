"""Trusted inputs and results for private runtime binding resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


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
    entity_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeBinding:
    """A private binding selected from the bot's existing runtime state."""

    binding_id: int
    source: RuntimeBindingSource


@dataclass(frozen=True, slots=True)
class RuntimeContainerRequest:
    """Resource selectors supplied by an already authorized server-side caller."""

    bot_id: str
    entity_id: str
    actor_user_id: str
    stage: str | None = None
    f_user_id: str | None = None
    device_uuid: str | None = None
    environment: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeContainerBinding:
    """Selected binding and engine metadata, without physical-device I/O."""

    binding: DeviceBindingRecord
    source: RuntimeBindingSource
    owner_id: str
    engine_type: str


@dataclass(frozen=True, slots=True)
class RuntimeContainerTarget:
    """One provider's physical target; a Teclaw target is not an ARCA ID."""

    provider: str
    provider_device_id: str
    device_uuid: str | None


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeContainers:
    runtime: RuntimeContainerBinding
    targets: tuple[RuntimeContainerTarget, ...]


__all__ = [
    "ResolvedRuntimeBinding",
    "RuntimeBindingRequest",
    "RuntimeBindingSource",
    "RuntimeBindingTarget",
    "RuntimeContainerRequest",
    "RuntimeContainerBinding",
    "RuntimeContainerTarget",
    "ResolvedRuntimeContainers",
]
