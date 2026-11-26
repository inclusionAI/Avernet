"""Deterministic candidate search used only by the pre-environment oracle switch."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.search_viewer import (
    SearchViewerType,
    normalize_search_viewer,
)
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    CandidateSearchResult,
    KeywordHit,
)


class FixedCandidateBotDiscover:
    """Return the configured candidates for every candidate-query search."""

    def __init__(
        self, candidates: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    ) -> None:
        self._candidates = list(candidates)
        self.candidates = self._candidates

    async def search_full_query(
        self,
        *,
        query: str,
        user_id: str = "",
        per_keyword_limit: int = 3,
        max_tokens: int = 5,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
        viewer_actor_type: SearchViewerType | None = None,
        viewer_actor_id: str | None = None,
        visibility: str | None = None,
    ) -> CandidateSearchResult:
        """Return the fixed candidate set for every non-empty query."""
        normalize_search_viewer(viewer_actor_type, viewer_actor_id)
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return CandidateSearchResult(
                candidates=[], tokens=[], raw_item_count=0, failed_keywords=[]
            )

        candidate_ids = tuple(
            str(item.get("bot_uuid") or item.get("bot_id") or "")
            for item in self._candidates
        )
        return CandidateSearchResult(
            candidates=self._candidates,
            tokens=[normalized_query],
            raw_item_count=len(self._candidates),
            failed_keywords=[],
            keyword_hits=(
                KeywordHit(
                    keyword=normalized_query,
                    item_count=len(self._candidates),
                    bot_ids=candidate_ids,
                    failed=False,
                ),
            ),
        )


__all__ = ["FixedCandidateBotDiscover"]
