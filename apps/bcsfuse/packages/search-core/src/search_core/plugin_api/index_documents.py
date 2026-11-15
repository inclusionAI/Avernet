"""Durable, business-opaque retrieval documents; independent of a vector engine."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from search_core.plugin_api.vector_point import VectorPoint


@dataclass(frozen=True)
class DocumentText:
    """Business converter output; key is stable within a source, not a DB ID."""

    key: str
    search_text: str
    rerank_text: str = ""

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key or len(self.key) > 128:
            raise ValueError("document key must contain 1-128 characters")
        if (
            not isinstance(self.search_text, str)
            or not self.search_text.strip()
            or len(self.search_text) > 8192
        ):
            raise ValueError("document search text must contain 1-8192 characters")
        if not isinstance(self.rerank_text, str) or len(self.rerank_text) > 8192:
            raise ValueError(
                "document rerank text must contain at most 8192 characters"
            )


@dataclass(frozen=True)
class IndexDocument:
    point: VectorPoint
    source_revision: int
    metadata: dict[str, Any] = field(default_factory=dict)
    is_deleted: bool = False
    version: int = 0
    modified_at: float = 0.0
    # None preserves legacy payload labels; {} explicitly clears filter labels.
    filters: dict[str, tuple[str, ...]] | None = None

    def to_index_point(self, filter_fields: Iterable[str] = ()) -> VectorPoint:
        """Project durable filter values into the engine's flat payload.

        The application supplies its filter field names so explicit empty filters
        also remove labels left in payload by older writers. No model work occurs.
        """
        if self.filters is None:
            return self.point
        payload = {
            k: v for k, v in self.point.payload.items() if k not in filter_fields
        }
        for key, values in self.filters.items():
            if key in payload:
                raise ValueError("filter field conflicts with document payload")
            payload[key] = list(values)
        return self.point.model_copy(update={"payload": payload})

    @property
    def id(self):
        return self.point.id

    @property
    def source_id(self):
        return self.point.payload.get("source_id", self.id)


@dataclass(frozen=True)
class DocumentVersion:
    """Lightweight sync row; no vector or business payload decoding."""

    id: str
    source_id: str
    version: int
    is_deleted: bool
    modified_at: float


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
    def load_by_source(self, source_id: str) -> list[IndexDocument]: ...
    def load_vector_candidates(
        self, source_id: str, embedding_key: str
    ) -> list[IndexDocument]:
        """Nondeleted candidates for this source/model, newest first, across this
        environment's generations (Doc IDs may repeat). Only for embedding reuse;
        callers must regenerate projections
        and compare text hashes and vector validity. Never use for serving/recovery.
        """
        ...

    def iter_batches(self, batch_size: int): ...
    def iter_versions(self, batch_size: int): ...
    def iter_version_batches(self, batch_size: int): ...
    def iter_change_versions_since(self, checkpoint: float, batch_size: int): ...
    def get_last_modified_time(self) -> float: ...
    def iter_change_batches_since(self, checkpoint: float, batch_size: int): ...
