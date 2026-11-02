"""Bounded executor for process-local snapshot retrieval routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.application.services.local_retrieval_snapshot import (
    direct_snapshot_hits,
    direct_snapshot_metadata,
    project_snapshot_hit,
    project_snapshot_recommendation,
    snapshot_hits,
    snapshot_metadata,
)
from app.infra.public.observability.search_stage_metrics import (
    search_stage_metrics,
)


Offload = Callable[[Callable[[], Any]], Awaitable[Any]]


class LocalSnapshotRouter:
    """Execute snapshot direct and fallback routes away from route policy."""

    def __init__(
        self,
        snapshot: Any,
        *,
        run: Offload,
        timeout: Callable[[str], float | None],
    ) -> None:
        self._snapshot = snapshot
        self._run = run
        self._timeout = timeout
        self.last_search_metadata: dict[str, Any] = {}
        self.last_recommendation_metadata: dict[str, Any] = {}

    async def search(
        self, *, query: str, top_k: int, filters: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        with search_stage_metrics("search", "local_snapshot"):
            hits, strategy = await self._run(
                lambda: snapshot_hits(
                    self._snapshot,
                    query=query,
                    limit=top_k,
                    filters=filters,
                ),
                self._timeout("local_snapshot"),
            )
        results = tuple(project_snapshot_hit(hit) for hit in hits)
        return results, snapshot_metadata(
            self._snapshot, hit_count=len(results), strategy=strategy
        )

    async def recommend(
        self, *, question: str, top_k: int, filters: dict[str, Any]
    ) -> list[Any]:
        with search_stage_metrics("recommend", "local_snapshot"):
            hits, strategy = await self._run(
                lambda: snapshot_hits(
                    self._snapshot,
                    query=question,
                    limit=top_k,
                    filters=filters,
                ),
                self._timeout("local_snapshot"),
            )
        recommendations = [
            project_snapshot_recommendation(hit) for hit in hits
        ]
        return recommendations, snapshot_metadata(
            self._snapshot,
            hit_count=len(recommendations),
            strategy=strategy,
        )

    async def direct_search(
        self, *, query: str, top_k: int, filters: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        with search_stage_metrics("search", "direct_snapshot"):
            hits, strategy = direct_snapshot_hits(
                self._snapshot,
                query=query,
                limit=top_k,
                filters=filters,
            )
        results = tuple(project_snapshot_hit(hit) for hit in hits)
        return results, direct_snapshot_metadata(
            self._snapshot, hit_count=len(results), strategy=strategy
        )


    async def direct_recommend(
        self, *, question: str, top_k: int, filters: dict[str, Any]
    ) -> tuple[list[Any], dict[str, Any]]:
        with search_stage_metrics("recommend", "direct_snapshot"):
            hits, strategy = direct_snapshot_hits(
                self._snapshot,
                query=question,
                limit=top_k,
                filters=filters,
            )
        recommendations = [
            project_snapshot_recommendation(hit) for hit in hits
        ]
        return recommendations, direct_snapshot_metadata(
            self._snapshot,
            hit_count=len(recommendations),
            strategy=strategy,
        )


__all__ = ["LocalSnapshotRouter"]
