"""BCSFuse-only Relay candidate retrieval.

Relay ``/search`` retains its required fields and candidate response; an optional
viewer pair scopes discovery through existing product policies. BCSFuse semantic search is
the sole candidate source for task candidate-query retrieval; empty or failed
semantic results stay empty instead of falling back to low-accuracy tokenized
catalog keyword search.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import uuid

from agentclaw.community.core.bot_public.catalog_metadata import BotCatalogCaller
from agentclaw.community.core.task.domain.search_viewer import SearchViewerType, normalize_search_viewer
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    CandidateSearchResult,
    KeywordHit,
)
from agentclaw.community.core.task.task_runner.client.catalog_keyword_discover import (
    CatalogKeywordBotDiscover,
    _DISPATCH_CATALOG_FILTERS,
    _runtime_ready,
)

logger = logging.getLogger("task.semantic_keyword.search")

SEMANTIC_TOP_K = 5
SEMANTIC_MAX_ATTEMPTS = 3
SEMANTIC_RETRY_BASE_DELAY_SECONDS = 1.0


class SemanticKeywordBotDiscover:
    """Query-only candidate retrieval backed by BCSFuse semantic search."""

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
        viewer_actor_type: SearchViewerType | None = None,
        viewer_actor_id: str | None = None,
    ) -> CandidateSearchResult:
        """Search BCSFuse with the full candidate query.

        Keyword search no longer acts as a fallback: its tokenized results are
        low-accuracy for this path and can select unusable executors. Semantic
        search is intentionally executed with the same system-view catalog rules
        as the keyword adapter. Public visibility and runtime readiness are
        post-filtered; Bot lifecycle credentials remain owned by their own
        domains. The semantic backend is capped at the five most similar, ready
        Bot candidates. The keyword-port parameters remain accepted for the
        existing candidate-search call shape.
        """
        viewer = normalize_search_viewer(viewer_actor_type, viewer_actor_id)
        normalized = str(query or "").strip()
        if not normalized:
            return CandidateSearchResult([], [], 0, [])

        result = await self._semantic_search(
            query=normalized,
            semantic_top_k=SEMANTIC_TOP_K,
            min_score=min_score,
            filters=filters,
            **viewer,
        )
        if result is not None:
            return result
        logger.warning(
            "semantic_search_unavailable query=%r; returning empty candidates",
            normalized[:500],
        )
        return self._empty_result(normalized)

    @staticmethod
    def _empty_result(normalized: str) -> CandidateSearchResult:
        return CandidateSearchResult(
            candidates=[],
            tokens=[normalized],
            raw_item_count=0,
            failed_keywords=[normalized],
            keyword_hits=(KeywordHit(normalized, 0, (), True),),
        )

    @staticmethod
    def _transport_failed(result: Any) -> bool:
        """Recognize BCSFuse failures that BotDiscoverService reports as empty.

        The真实 BCSFuse adapter intentionally swallows connection/HTTP/timeout
        errors and returns an empty result with ``recommend_response=None``.
        Treating that value like a successful empty search would make retries
        ineffective; distinguish it from a valid empty recommendation payload.
        """
        if not isinstance(result, dict):
            return result is None
        context = result.get("context")
        return (
            isinstance(context, dict)
            and context.get("recommend_response") is None
        )

    async def _semantic_search(
        self,
        *,
        query: str,
        semantic_top_k: int,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
        viewer_actor_type: SearchViewerType | None = None,
        viewer_actor_id: str | None = None,
    ) -> CandidateSearchResult | None:
        viewer = normalize_search_viewer(viewer_actor_type, viewer_actor_id)
        if self._semantic_discover is None:
            logger.warning(
                "semantic_search_not_configured query=%r; returning empty candidates",
                query[:500],
            )
            return None
        result: Any = None
        for attempt in range(1, SEMANTIC_MAX_ATTEMPTS + 1):
            request_id = f"task-semantic-{uuid.uuid4().hex[:12]}-{attempt}"
            logger.debug(
                "semantic_search_start request_id=%s attempt=%d/%d query=%r top_k=%d",
                request_id,
                attempt,
                SEMANTIC_MAX_ATTEMPTS,
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
                    **({"runtime_state": "online", **viewer} if viewer else {
                        "filters": filters if filters is not None else {"runtime_state": ["online"]},
                        "catalog_filters": _DISPATCH_CATALOG_FILTERS,
                    }),
                    caller=BotCatalogCaller(tenant_id="", user_id=None, app_id=None),
                    request_id=request_id,
                )
                if not self._transport_failed(result):
                    break
                logger.warning(
                    "semantic_search_backend_failed request_id=%s attempt=%d/%d "
                    "query=%r",
                    request_id,
                    attempt,
                    SEMANTIC_MAX_ATTEMPTS,
                    query[:500],
                )
            except Exception as exc:  # noqa: BLE001 - transient semantic failures are retried bounded
                logger.warning(
                    "semantic_search_failed request_id=%s attempt=%d/%d "
                    "query=%r error_type=%s error=%s",
                    request_id,
                    attempt,
                    SEMANTIC_MAX_ATTEMPTS,
                    query[:500],
                    type(exc).__name__,
                    str(exc)[:500],
                    exc_info=True,
                )
                if attempt < SEMANTIC_MAX_ATTEMPTS:
                    await asyncio.sleep(
                        SEMANTIC_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    )
        if self._transport_failed(result):
            return None

        raw_items = result.get("items") if isinstance(result, dict) else []
        valid_items = [
            item
            for item in (raw_items or [])
            if isinstance(item, dict)
            and _runtime_ready(item)
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
