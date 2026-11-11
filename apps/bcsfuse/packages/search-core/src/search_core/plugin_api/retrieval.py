"""Synchronous index operations; applications supply business filters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from search_core.plugin_api.vector_point import VectorPoint
    from search_core.plugin_api.vector_search_hit import VectorSearchHit


class RetrievalIndex(Protocol):
    def vector_search(
        self,
        vector: list[float],
        limit: int,
        filters: dict[str, tuple[str, ...]],

    ) -> list[VectorSearchHit]: ...

    def keyword_search(
        self,
        query: str,
        limit: int,
        filters: dict[str, tuple[str, ...]],

    ) -> list[VectorSearchHit]: ...


@runtime_checkable
class IndexStatistics(Protocol):
    """Optional exact counts of the currently bound vector and keyword collections.

    Return vector_points and keyword_points. With a nonempty index_key, also return
    vector/keyword_matching_points and vector/keyword_mismatched_points from actual
    payload filters. Missing payload keys count as mismatched. Counts are collected
    sequentially, not a transactional snapshot across external Qdrant writers.
    Failures raise; never report zero
    for an unavailable collection. Counts describe index points, not source Cards.
    """

    def point_counts(self, index_key: str = "") -> dict[str, int]: ...


@runtime_checkable
class IndexInspection(Protocol):
    """Read actual vector/keyword points for one source, including stale points.

    Missing points are omitted. Read failures raise rather than masquerading as
    empty collections. Reads are not an atomic snapshot across collections.
    """

    def read_source_points(self, source_id: str) -> dict[str, list[VectorPoint]]: ...
