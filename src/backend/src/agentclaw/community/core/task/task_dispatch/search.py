"""Pure candidate search service used by centralized and Relay adapters."""

from __future__ import annotations

import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.task_dispatch.search_registry import (
    DEFAULT_SEARCH_STRATEGY,
    TaskSearchRegistry,
)
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    search_candidates,
)


logger = logging.getLogger("task.relay.search")


class TaskSearch:
    """Query-only search facade.

    It deliberately does not accept task/node/holder identifiers and does not
    decide HIT_SINGLE/HIT_MULTI_BOTS/MISS.  Those decisions belong to the
    centralized dispatcher or the Relay Skill.
    """

    def __init__(
        self,
        discover: Any = None,
        *,
        user_id: str = "",
        registry: TaskSearchRegistry | None = None,
    ) -> None:
        self._registry = registry or TaskSearchRegistry(discover)
        self._user_id = user_id

    @property
    def available(self) -> bool:
        return self._registry.resolve() is not None

    @property
    def backend_name(self) -> str:
        return self._registry.backend_name()

    async def search(
        self, query: str, *, strategy: str = DEFAULT_SEARCH_STRATEGY
    ) -> Any:
        normalized = str(query or "").strip()
        if not normalized:
            raise ValueError("search query is required")
        discover = self._registry.resolve(strategy)
        if discover is None:
            # Keep the existing search result shape for callers that already
            # project .candidates/.tokens/.failed_keywords.
            return await search_candidates(None, normalized, user_id=self._user_id)
        return await search_candidates(
            discover,
            normalized,
            user_id=self._user_id,
        )

    @staticmethod
    def _project_candidate(item: dict[str, Any]) -> dict[str, Any]:
        candidate: dict[str, Any] = {
            "bot_uuid": item.get("bot_uuid") or item.get("bot_id")
        }
        for field_name in ("bot_name", "bot_desc", "bot_type", "status"):
            if field_name in item:
                candidate[field_name] = item[field_name]
        if isinstance(item.get("recommend"), dict):
            candidate["recommend"] = item["recommend"]
        return candidate

    async def search_catalog(
        self, query: str, *, strategy: str = DEFAULT_SEARCH_STRATEGY
    ) -> dict[str, Any]:
        """Return the public query-only candidate catalog with diagnostics."""
        started_at = time.monotonic()
        discover = self._registry.resolve(strategy)
        backend_name = (
            type(discover).__name__ if discover is not None else "None"
        )
        normalized = str(query or "").strip()
        if not normalized:
            logger.debug("query_rejected reason=empty_query")
            raise TaskStateError("search query is required")
        logger.debug(
            "search_start discover=%s query=%r query_length=%d filters=%s",
            backend_name,
            normalized[:500],
            len(normalized),
            {"runtime_state": ["online"]},
        )
        if discover is None:
            logger.warning(
                "search_empty reason=discover_unavailable discover=%s elapsed_ms=%.1f",
                backend_name,
                (time.monotonic() - started_at) * 1000,
            )
            return self._response([], result=None)
        result = await self.search(normalized, strategy=strategy)
        candidates = [
            self._project_candidate(item)
            for item in result.candidates
            if isinstance(item, dict) and (item.get("bot_uuid") or item.get("bot_id"))
        ][:20]
        logger.debug(
            "search_complete discover=%s query=%r tokens=%s raw_item_count=%d projected=%d "
            "failed_keywords=%s candidate_ids=%s elapsed_ms=%.1f",
            backend_name,
            normalized[:500],
            result.tokens,
            result.raw_item_count,
            len(candidates),
            result.failed_keywords,
            [item.get("bot_uuid") for item in candidates],
            (time.monotonic() - started_at) * 1000,
        )
        if not candidates:
            logger.info(
                "search_empty reason=no_matching_candidates discover=%s query=%r tokens=%s "
                "raw_item_count=%d failed_keywords=%s elapsed_ms=%.1f",
                backend_name,
                normalized[:500],
                result.tokens,
                result.raw_item_count,
                result.failed_keywords,
                (time.monotonic() - started_at) * 1000,
            )
        return self._response(candidates, result=result)

    @staticmethod
    def _response(candidates: list[dict[str, Any]], *, result: Any = None) -> dict[str, Any]:
        """Response dict with additive retrieval diagnostics (轨迹采样素材).

        ``candidates``/``total`` 契约不变;新增 ``tokens``/``raw_item_count``/
        ``failed_keywords``/``keyword_hits`` 为加法键,旧消费方按键读取不受影响。
        ``keyword_hits`` 与 ``DispatchRationale.search_sampling`` 的 keywords 同形。
        """
        keyword_hits = [
            {
                "keyword": k.keyword,
                "item_count": k.item_count,
                "bot_ids": list(k.bot_ids),
                "failed": bool(k.failed),
            }
            for k in (getattr(result, "keyword_hits", None) or ())
        ]
        return {
            "candidates": candidates,
            "total": len(candidates),
            "tokens": list(getattr(result, "tokens", None) or []),
            "raw_item_count": int(getattr(result, "raw_item_count", None) or 0),
            "failed_keywords": list(getattr(result, "failed_keywords", None) or []),
            "keyword_hits": keyword_hits,
        }
