"""Process-local literal fallback used when semantic admission cannot proceed."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.models.profile_fragment import FragmentMatch
from app.application.services.worker_vector_match_types import MatchResult
from app.domain.services.retrieval_logging import log_stage

logger = logging.getLogger("app.application.services.worker_vector_match_service")


class LiteralFallbackMixin:
    def _literal_fallback_matches(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any],
        excluded: set[str],
        allowed: set[str] | None,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[MatchResult]:
        """Build a local result without embedding, rerank, or Qdrant."""
        fallback_reason = "literal_fallback"
        try:
            hits = self._fallback_index.keyword_search(
                query=query,
                limit=top_k,
                filters=filters,
                allowed_ids=None,
            )
            if not hits:
                default_search = getattr(self._fallback_index, "default_search", None)
                if callable(default_search):
                    hits = default_search(limit=top_k, filters=filters, allowed_ids=None)
                    fallback_reason = "literal_fallback_default"
                    if diagnostics is not None:
                        diagnostics["literal_fallback_default_used"] = bool(hits)
        except Exception as error:
            log_stage(
                logger,
                "literal_fallback",
                available=False,
                error_type=type(error).__name__,
            )
            logger.error("[FRAGMENT-MATCH] literal fallback failed: %s", error)
            raise

        results: list[MatchResult] = []
        for hit in hits:
            payload = dict(hit.payload or {})
            profile_key = payload.get("profile_key") or hit.id
            if profile_key in excluded or (
                allowed is not None and profile_key not in allowed
            ):
                continue
            metadata = self._metadata_store.get(profile_key)
            if metadata is None:
                metadata = self._build_metadata_from_payload(profile_key, payload)
            if metadata is None:
                continue
            content = str(
                payload.get("content")
                or payload.get("searchable_text")
                or payload.get("content_preview")
                or ""
            )
            fragment = FragmentMatch(
                fragment_type="full",
                fragment_id=hit.id,
                score=hit.score,
                content_preview=content[:200],
                content=content,
            )
            results.append(
                MatchResult(
                    profile_key=profile_key,
                    metadata=metadata,
                    score=hit.score,
                    aggregated_score=hit.score,
                    reasons=[fallback_reason],
                    fragment_matches=[fragment],
                )
            )
            if len(results) >= top_k:
                break
        log_stage(
            logger,
            "literal_fallback",
            hit_count=len(hits),
            result_count=len(results),
            available=True,
        )
        return results


__all__ = ["LiteralFallbackMixin"]
