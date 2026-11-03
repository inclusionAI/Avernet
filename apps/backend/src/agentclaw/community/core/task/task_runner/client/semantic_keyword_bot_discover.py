"""BCSFuse-first candidate retrieval with catalog keyword fallback.

The Relay ``/search`` HTTP contract stays unchanged. This adapter only changes
the internal query/retrieval strategy: one candidate-query semantic recommendation
is preferred, and the existing tokenized catalog keyword search remains the
fallback when BCSFuse is unavailable, returns no ready candidate, or fails.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import uuid

from agentclaw.community.core.bot_public.catalog_metadata import BotCatalogCaller
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    CandidateSearchResult,
    KeywordHit,
    search_candidates,
)
from agentclaw.community.core.task.task_runner.client.catalog_keyword_discover import (
    CatalogKeywordBotDiscover,
    _credential_ready,
    _DISPATCH_CATALOG_FILTERS,
    _runtime_ready,
)

logger = logging.getLogger("task.semantic_keyword.search")

SEMANTIC_TOP_K = 5


class SemanticKeywordBotDiscover:
    """Query-only candidate retrieval: semantic first, keyword as fallback."""

    def __init__(
        self,
        keyword_discover: CatalogKeywordBotDiscover,
        semantic_discover: Any,
    ) -> None:
        self._keyword_discover = keyword_discover
        self._semantic_discover = semantic_discover

    def search_by_keyword(
        self,
        *,
        keyword: str,
        user_id: str,
        top_k: int = 10,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
        fallback_to_all: bool = False,
    ) -> dict[str, Any]:
        """Preserve the existing keyword port for direct callers.

        Candidate-query semantic-first behavior is exposed below. This method keeps
        the adapter compatible with all existing ``search_by_keyword`` callers.
        """
        return self._keyword_discover.search_by_keyword(
            keyword=keyword,
            user_id=user_id,
            top_k=top_k,
            min_score=min_score,
            filters=filters,
            fallback_to_all=fallback_to_all,
        )

    async def search_full_query(
        self,
        *,
        query: str,
        user_id: str = "",
        per_keyword_limit: int = 3,
        max_tokens: int = 5,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
    ) -> CandidateSearchResult:
        """Use BCSFuse on the passed candidate query before token fallback.

        Semantic search is intentionally executed with the same system-view
        catalog rules as the keyword adapter. Public availability, runtime
        readiness, and persisted ``agent_code`` are post-filtered so BCSFuse
        cannot make a non-executable Bot relayable. The semantic backend is
        capped at the five most similar, ready Bot candidates; token fallback
        retains its existing keyword limits.
        """
        normalized = str(query or "").strip()
        if not normalized:
            return CandidateSearchResult([], [], 0, [])

        result = await self._semantic_search(
            query=normalized,
            semantic_top_k=SEMANTIC_TOP_K,
            min_score=min_score,
            filters=filters,
        )
        if result is not None and result.candidates:
            return result
        if result is not None:
            logger.info(
                "semantic_search_empty query=%r semantic_raw_count=%d; "
                "falling back to keyword search",
                normalized[:500],
                result.raw_item_count,
            )
        return await search_candidates(
            self._keyword_discover,
            normalized,
            user_id=user_id,
            per_keyword_limit=per_keyword_limit,
            max_tokens=max_tokens,
            min_score=min_score,
            filters=filters,
        )

    async def _semantic_search(
        self,
        *,
        query: str,
        semantic_top_k: int,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
    ) -> CandidateSearchResult | None:
        request_id = f"task-semantic-{uuid.uuid4().hex[:12]}"
        logger.debug(
            "semantic_search_start request_id=%s query=%r top_k=%d",
            request_id,
            query[:500],
            semantic_top_k,
        )
        try:
            result = await asyncio.to_thread(
                self._semantic_discover.search_by_keyword,
                keyword=query,
                user_id="",
                top_k=semantic_top_k,
                min_score=min_score,
                filters=filters
                if filters is not None
                else {"runtime_state": ["online"]},
                catalog_filters=_DISPATCH_CATALOG_FILTERS,
                caller=BotCatalogCaller(tenant_id="", user_id=None, app_id=None),
                request_id=request_id,
            )
        except Exception as exc:  # noqa: BLE001 - semantic availability cannot break dispatch
            logger.warning(
                "semantic_search_failed request_id=%s query=%r error_type=%s error=%s",
                request_id,
                query[:500],
                type(exc).__name__,
                str(exc)[:500],
                exc_info=True,
            )
            return None

        raw_items = result.get("items") if isinstance(result, dict) else []
        valid_items = [
            item
            for item in (raw_items or [])
            if isinstance(item, dict)
            and _runtime_ready(item)
            and _credential_ready(item)
        ]
        ready_items = _dedupe_candidates(valid_items)[:SEMANTIC_TOP_K]
        bot_ids = tuple(
            str(item.get("bot_uuid") or item.get("bot_id") or "")
            for item in ready_items
        )
        logger.debug(
            "semantic_search_complete request_id=%s query=%r raw_count=%d "
            "ready_count=%d candidate_ids=%s",
            request_id,
            query[:500],
            len(raw_items or []),
            len(ready_items),
            list(bot_ids),
        )
        return CandidateSearchResult(
            candidates=ready_items,
            # Diagnostic ``tokens`` records actual retrieval terms. Semantic
            # search used the entire candidate query as its only term, so it
            # must stay aligned one-to-one with ``keyword_hits``.
            tokens=[query],
            raw_item_count=len(raw_items or []),
            failed_keywords=[],
            keyword_hits=(KeywordHit(query, len(ready_items), bot_ids, False),),
        )


def _score(item: dict[str, Any]) -> float:
    value = (item.get("recommend") or {}).get("score", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dedupe_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        identity = str(item.get("bot_uuid") or item.get("bot_id") or "").strip()
        if identity and identity not in seen:
            seen[identity] = item
    return sorted(seen.values(), key=_score, reverse=True)
