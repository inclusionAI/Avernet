"""Plugin API for notifying ECB that one TC resource is ready."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin

TC_RESOURCE_READY_SCHEMA_VERSION = "1"
_EVENT_ID_PATTERN = re.compile(r"^tc\.resource\.ready:[A-Za-z0-9._:-]+$")


@dataclass(frozen=True, slots=True)
class TcResourceReadyEvent:
    """Stable cross-service event envelope consumed by ECB."""

    schema_version: str
    event_id: str
    res_id: str

    @classmethod
    def for_resource(cls, res_id: str) -> "TcResourceReadyEvent":
        normalized = res_id.strip()
        if not normalized:
            raise ValueError("res_id_required")
        return cls(
            schema_version=TC_RESOURCE_READY_SCHEMA_VERSION,
            event_id=f"tc.resource.ready:{normalized}",
            res_id=normalized,
        )

    def __post_init__(self) -> None:
        if self.schema_version != TC_RESOURCE_READY_SCHEMA_VERSION:
            raise ValueError("unsupported_schema_version")
        if not self.res_id or self.res_id != self.res_id.strip():
            raise ValueError("res_id_invalid")
        if self.event_id != f"tc.resource.ready:{self.res_id}":
            raise ValueError("event_id_mismatch")
        if not _EVENT_ID_PATTERN.fullmatch(self.event_id):
            raise ValueError("event_id_invalid")

    def as_payload(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "res_id": self.res_id,
        }


@runtime_checkable
class TcResourceReadyPublisherPlugin(Plugin, Protocol):
    """Publish the stable resource-ready event to the configured ECB boundary."""

    async def publish(self, event: TcResourceReadyEvent) -> None: ...
