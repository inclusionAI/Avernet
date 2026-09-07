"""Caller-resolved inputs carried through Bot creation.

``BotCreateContext`` is the surface-resolved business context; ``BotCreateSpec``
is the attribute bag each API surface maps its request into. Both live here, off
``create_flow``, so ``create_flow`` holds the orchestration and the two input
contracts stay together as one concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentclaw.community.core.bot_management.engines.provisioning import (
    BotCreateTemplateValidationMode,
)


class BotCreateDeploymentMode(StrEnum):
    """Deployment boundary relevant to Bot creation policy."""

    CLOUD = "cloud"
    LOCAL = "local"


@dataclass(frozen=True)
class BotCreateContext:
    """Caller-resolved business context required by creation policy."""

    deployment_mode: BotCreateDeploymentMode
    space_kind: str
    # Legacy callers keep BotService's established owner/device limit behavior.
    space_quota: bool = False
    # Whether this surface offers the "create as service" coding intake: a
    # coding create (engine_properties present) with bot_type="service" is
    # translated to a personal create — the only shape the engine-strategy
    # combination gate admits — and, once created and owned, upgraded through
    # the ServiceIntakeSeam. Surfaces that do not opt in keep the historical
    # 409. The seam must be wired for the translation to engage: an opted-in
    # surface without one is a misconfiguration the gate refuses, not an
    # intent to fulfill.
    service_intake: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "deployment_mode": self.deployment_mode.value,
            "space_kind": self.space_kind,
            "space_quota": self.space_quota,
            "service_intake": self.service_intake,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BotCreateContext":
        return cls(
            deployment_mode=BotCreateDeploymentMode(payload["deployment_mode"]),
            space_kind=payload["space_kind"],
            space_quota=bool(payload.get("space_quota", False)),
            service_intake=bool(payload.get("service_intake", False)),
        )


@dataclass(frozen=True)
class BotCreateSpec:
    """The bot attributes a create / authorization-completion runs with.

    An explicit contract instead of an untyped payload dict: each API surface
    maps its own request shape into this, so a field added or renamed here is a
    type error at every call site rather than a key that silently goes missing
    on one surface.

    ``entity_id`` and ``engine_type`` are **required and concrete** — each
    surface resolves its own default while building the spec (the caller's id;
    ``DEFAULT_ENGINE_TYPE``). ``BotService.create_bot`` only ever applies
    ``x or <default>`` to them, so a concrete value is equivalent to leaving
    them unset, and the flow never has to reason about an absent engine.

    Two fields keep an unset state on purpose:

    * ``bot_name`` — ``None`` means "no name given" so ``_resolve_bot_name``
      derives one (the owner's nick name for a first bot, else the bot id). No
      string can stand in: ``validate_bot_name("")`` rejects the request, and
      the default needs a first-bot lookup the caller cannot pre-compute.
    * ``bot_desc`` — stored straight through to a nullable column and echoed
      back in responses, so ``None`` ("no description") and ``""`` are
      genuinely different persisted values, not interchangeable defaults.
    """

    entity_id: str
    engine_type: str
    bot_type: str
    bot_name: str | None
    entity_type: str = "staff"
    bot_desc: str | None = None
    avatar_url: str | None = None
    share_policy: dict[str, Any] | None = None
    template_type: str | None = None
    template_config: dict[str, Any] | None = None
    # How strictly template ownership rules apply: PUBLIC for OpenAPI inputs,
    # LEGACY for established internal snapshots (may carry ``template_uid``).
    template_validation_mode: BotCreateTemplateValidationMode = (
        BotCreateTemplateValidationMode.LEGACY
    )
    space_id: int | None = None
    # Engine-owned creation properties (the public ``engine_properties``
    # contract), kept opaque here: only the engine-selected
    # ``EngineProvisioningStrategy`` may interpret its keys. The legacy
    # template_type/template_config pair above serves established internal
    # callers and is mutually exclusive with this bag.
    engine_properties: dict[str, Any] = field(default_factory=dict)
