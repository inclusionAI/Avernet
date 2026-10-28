"""Business-neutral values. Identity is supplied by trusted composition."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from search_core.plugin_api.vector_search_hit import VectorSearchHit


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    # None intentionally means unrestricted; an empty set means no access.
    # IDs identify ranked items (full Profile key in TC, Agent ID in Agent Search).
    allowed_ids: frozenset[str] | None

    def __post_init__(self):
        if not self.subject:
            raise ValueError("verified identity requires a subject")


@dataclass(frozen=True)
class SearchCommand:
    query: str
    limit: int
    operation: Literal["search", "recommend"]
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)
    scene: str = "default"
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.query.strip() or self.limit <= 0:
            raise ValueError("query and a positive limit are required")
        if self.operation not in {"search", "recommend"}:
            raise ValueError("unsupported search operation")


@dataclass(frozen=True)
class SearchItem:
    item_id: str
    name: str
    summary: str
    score: float
    score_source: str
    extensions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.item_id or not self.score_source or not math.isfinite(self.score):
            raise ValueError(
                "item identity, score source and finite score are required"
            )


@dataclass(frozen=True)
class SearchResult:
    items: tuple[SearchItem, ...] = ()
    status: Literal["ok", "degraded"] = "ok"
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    limit: int
    filters: dict[str, tuple[str, ...]]
    allowed_ids: frozenset[str] | None


@dataclass(frozen=True)
class RecallResult:
    """Staged recall output; available distinguishes a valid empty from outage."""

    vector_hits: tuple[VectorSearchHit, ...] = ()
    keyword_hits: tuple[VectorSearchHit, ...] = ()
    query_vector: tuple[float, ...] = ()
    reasons: tuple[str, ...] = ()
    available: bool = True
