"""Event payload types for the in-process event bus."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceActivatedEvent:
    """Published when a device binding first transitions PENDING -> ACTIVE.

    Emitted from DeviceService.report_device_alive after the state flip and
    after the existing bot-status / bot-config sync hooks.

    Attributes:
        device_id: The device identifier (e.g. "staff_u001_default").
        binding_id: Primary key of the ac_entity_device_binding row.
        entity_id: Owning entity id (staff_id for personal bots).
        entity_type: "staff" | "team" | "proj" | etc.
        device_provider: "arca" | "daas" | "local".
        sandbox_id: Sandbox id from device_props, or None for non-sandbox devices.
    """

    device_id: str
    binding_id: int
    entity_id: str
    entity_type: str
    device_provider: str
    sandbox_id: str | None = None


@dataclass(frozen=True)
class DeviceAliveEvent:
    """A validated PENDING device reported alive before its ACTIVE commit.

    Required durable consumers may reject a failed hand-off.  In that case
    the caller keeps the binding PENDING, so the runtime's retry redelivers the
    event without inserting work for every later ACTIVE heartbeat.
    """

    device_id: str
    binding_id: int
    entity_id: str
    entity_type: str
    device_provider: str
    sandbox_id: str | None = None


@dataclass(frozen=True)
class BaasPublishCompletedEvent:
    """A current BaaS create/restart publish reached its successful terminal state.

    This is only a lifecycle wake-up. Consumers must re-read the current Bot
    and binding before making any business decision.
    """

    binding_id: int
    bot_id: str
    owner_id: str
    publish_id: int
    publish_kind: str
