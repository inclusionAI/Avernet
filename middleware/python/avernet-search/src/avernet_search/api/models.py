"""Business-neutral values. Identity is supplied by trusted composition."""

import math
from dataclasses import dataclass, field
from typing import Any, Literal


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
