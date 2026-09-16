"""Outbound contract for publishing one TC resource-ready fact."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

TC_RESOURCE_READY_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class TcResourceReadyEvent:
    """The complete cross-service event; ECB resolves all other facts."""

    schema_version: str
    event_id: str
    res_id: str

    def __post_init__(self) -> None:
        if not self.res_id:
            raise ValueError("resource_id_required")
        if self.schema_version != TC_RESOURCE_READY_SCHEMA_VERSION:
            raise ValueError("unsupported_schema_version")
        if self.event_id != f"tc.resource.ready:{self.res_id}":
            raise ValueError("event_id_must_match_resource_id")

    @classmethod
    def for_resource(cls, res_id: str) -> "TcResourceReadyEvent":
        if not res_id:
            raise ValueError("resource_id_required")
        return cls(
            schema_version=TC_RESOURCE_READY_SCHEMA_VERSION,
            event_id=f"tc.resource.ready:{res_id}",
            res_id=res_id,
        )

    def as_payload(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "res_id": self.res_id,
        }


@runtime_checkable
class TcResourceReadyPublisherPort(Protocol):
    """Publish one stable resource-ready event to an external consumer."""

    @abstractmethod
    async def publish(self, event: TcResourceReadyEvent) -> None: ...


__all__ = [
    "TC_RESOURCE_READY_SCHEMA_VERSION",
    "TcResourceReadyEvent",
    "TcResourceReadyPublisherPort",
]
