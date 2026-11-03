"""Route the lexical retrieval branch through the fastest safe source."""

from __future__ import annotations

import logging

from collections.abc import Callable
from typing import Any

from app.application.services.keyword_candidate_selection import (
    KeywordRetrievalOutcome,
    keyword_candidates_with_status,
    local_keyword_candidates_with_status,
)
from app.application.services.fragment_retrieval_branches import run_retrieval_branches
from app.application.services.retrieval_branch_runner import RetrievalBranchResult
from app.domain.services.retrieval_logging import log_stage

logger = logging.getLogger(__name__)


class FragmentKeywordRoutingMixin:
    """Choose vector-store or process-local keyword retrieval explicitly."""

    def _route_fragment_keyword(
        self,
        *,
        query: str,
        query_embedding: list[float],
        keyword_budget: int,
        final_top_k: int,
        filters: dict[str, Any] | None,
        excluded: set[str],
        allowed: set[str] | None,
        enabled_types: set[str],
        run_dense: Callable[[], list[Any]],
    ) -> tuple[RetrievalBranchResult[Any], RetrievalBranchResult[Any]]:
        """Run dense and lexical retrieval without paying avoidable RPCs.

        Without a process-local profile view, dense and authoritative keyword
        retrieval stay parallel. With a local view, dense health is observed
        first; a sufficient local keyword result avoids the second Qdrant RPC.
        An insufficient local view still consults the authoritative store and
        can contribute additional candidates.
        """

        def run_remote_keyword() -> KeywordRetrievalOutcome:
            return keyword_candidates_with_status(
                self._vector_store,
                query,
                keyword_budget,
                filters,
                excluded,
                allowed,
                enabled_types,
            )

        if self._fallback_index is None:
            return run_retrieval_branches(
                run_dense, run_remote_keyword, self._retrieval_runner
            )

        def run_local_keyword() -> KeywordRetrievalOutcome:
            outcome = local_keyword_candidates_with_status(
                self._fallback_index,
                query,
                keyword_budget,
                filters,
                excluded,
                allowed,
                enabled_types,
                sufficient_limit=final_top_k,
            )
            log_stage(
                logger,
                "keyword_search",
                available=outcome.available,
                source=outcome.source,
                candidate_count=len(outcome.candidates),
                profile_budget=keyword_budget,
            )
            return outcome

        def merge_keyword(
            local_outcome: KeywordRetrievalOutcome,
            remote_outcome: KeywordRetrievalOutcome,
        ) -> KeywordRetrievalOutcome:
            if not local_outcome.candidates:
                return remote_outcome
            if not remote_outcome.candidates:
                return local_outcome
            merged = {
                candidate.profile_key: candidate
                for candidate in remote_outcome.candidates
            }
            for candidate in local_outcome.candidates:
                merged.setdefault(candidate.profile_key, candidate)
            return KeywordRetrievalOutcome(
                list(merged.values())[:keyword_budget],
                available=remote_outcome.available,
                source="hybrid_local",
                local_count=len(local_outcome.candidates),
            )

        dense_result: RetrievalBranchResult[Any]
        try:
            dense_result = RetrievalBranchResult(value=run_dense())
        except BaseException as error:  # noqa: BLE001 -- branch result
            dense_result = RetrievalBranchResult(error=error)

        local_keyword = (
            run_local_keyword()
            if not query_embedding
            or (
                query_embedding
                and dense_result.error is None
                and dense_result.value
            )
            else None
        )
        if local_keyword is not None and local_keyword.local_keyword_sufficient:
            keyword_outcome = local_keyword
        else:
            keyword_outcome = run_remote_keyword()
            if local_keyword is not None:
                keyword_outcome = merge_keyword(local_keyword, keyword_outcome)
        return dense_result, RetrievalBranchResult(value=keyword_outcome)


__all__ = ["FragmentKeywordRoutingMixin"]
