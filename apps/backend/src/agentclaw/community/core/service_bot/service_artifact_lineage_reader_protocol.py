"""Service API contract for exact Skill lineage in replayable artifacts."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ServiceArtifactReference:
    publish_id: int
    source_bot_id: str
    source_bot_name: str
    service_version: int | None
    sc_version_number: str


@dataclass(frozen=True, slots=True)
class UnknownServiceArtifact:
    resource_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class ServiceArtifactLineage:
    references: tuple[ServiceArtifactReference, ...]
    unknown: tuple[UnknownServiceArtifact, ...]


@runtime_checkable
class ServiceArtifactLineageReaderProtocol(Protocol):
    @abstractmethod
    def scan(self, *, skill_uuid: str, env: str) -> ServiceArtifactLineage: ...


__all__ = [
    "ServiceArtifactLineage",
    "ServiceArtifactLineageReaderProtocol",
    "ServiceArtifactReference",
    "UnknownServiceArtifact",
]
