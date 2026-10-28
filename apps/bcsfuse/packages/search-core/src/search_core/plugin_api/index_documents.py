"""Durable, business-opaque retrieval documents; independent of a vector engine."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from search_core.plugin_api.vector_point import VectorPoint


@dataclass(frozen=True)
class IndexDocument:
    point: VectorPoint
    source_revision: int
    metadata: dict[str, Any] = field(default_factory=dict)
    is_deleted: bool = False
    version: int = 0
    modified_at: float = 0.0

    @property
    def id(self):
        return self.point.id


@dataclass(frozen=True)
class DocumentChanges:
    # Includes deletion documents so consumers can restore their auxiliary state.
    upserts: list[IndexDocument]
    checkpoint: float
    deleted_ids: list[str] = field(default_factory=list)


class IndexDocuments(Protocol):
    environment: str
    index_key: str

    def load_by_id(self, id: str) -> IndexDocument | None: ...
    def iter_batches(self, batch_size: int): ...
    def iter_versions(self, batch_size: int): ...
    def get_last_modified_time(self) -> float: ...
    def iter_change_batches_since(self, checkpoint: float, batch_size: int): ...
