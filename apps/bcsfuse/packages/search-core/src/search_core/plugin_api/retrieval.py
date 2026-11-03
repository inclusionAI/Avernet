"""Synchronous index operations; applications supply authorized IDs and filters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

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


@runtime_checkable
class IndexStatistics(Protocol):
    """Optional exact counts of the currently bound vector and keyword collections.

    Return vector_points and keyword_points. Failures raise; never report zero
    for an unavailable collection. Counts describe index points, not source Cards.
    """

    def point_counts(self) -> dict[str, int]: ...
