"""Synchronous index operations; applications supply authorized IDs and filters."""

from __future__ import annotations
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from search_core.plugin_api.vector_search_hit import VectorSearchHit


class RetrievalIndex(Protocol):
    def vector_search(
        self,
        vector: list[float],
        limit: int,
        filters: dict[str, tuple[str, ...]],
        allowed_ids: frozenset[str] | None,
    ) -> list[VectorSearchHit]: ...

    def keyword_search(
        self,
        query: str,
        limit: int,
        filters: dict[str, tuple[str, ...]],
        allowed_ids: frozenset[str] | None,
    ) -> list[VectorSearchHit]: ...
