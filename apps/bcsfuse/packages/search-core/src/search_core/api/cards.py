"""Card management Service API, independent of HTTP and storage adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

if TYPE_CHECKING:
    from search_core.catalog.cards import CardSnapshot

PreparedT = TypeVar("PreparedT")


class CatalogConflict(ValueError):
    """Resource identity or an internal compare-and-swap revision conflicts."""


class IndexTargetMismatch(CatalogConflict):
    """The requested repair generation is not loaded on the responding instance."""


@dataclass(frozen=True)
class CardRecord(Generic[PreparedT]):
    card: CardSnapshot
    revision: int
    prepared_revision: int
    prepared: PreparedT | None
    error_code: str
    profile: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    preparation_key: str = ""


class CardManagement(Protocol):
    def read(self, agent_id: str) -> CardRecord: ...
    def upsert(
        self,
        agent_id: str,
        fields: dict[str, Any],
        resource_type: str | None,
        search_config: dict[str, Any],
        *,
        labels: dict[str, list[str]] | None = None,
    ) -> CardRecord: ...
    def remove(self, agent_id: str) -> None: ...
    def status(self, agent_id: str) -> dict: ...
