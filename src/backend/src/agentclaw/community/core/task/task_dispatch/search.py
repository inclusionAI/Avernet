"""Pure candidate search service used by centralized and Relay adapters."""

from __future__ import annotations

import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
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

    def __init__(self, discover: Any, *, user_id: str = "") -> None:
        self._discover = discover
        self._user_id = user_id

    @property
    def available(self) -> bool:
        return self._discover is not None

    @property
    def backend_name(self) -> str:
        return type(self._discover).__name__ if self._discover is not None else "None"

    async def search(self, query: str) -> Any:
        normalized = str(query or "").strip()
        if not normalized:
            raise ValueError("search query is required")
        if self._discover is None:
            # Keep the existing search result shape for callers that already
            # project .candidates/.tokens/.failed_keywords.
            return await search_candidates(None, normalized, user_id=self._user_id)
        return await search_candidates(
            self._discover,
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

    async def search_catalog(self, query: str) -> dict[str, Any]:
        """Return the public query-only candidate catalog with diagnostics."""
        started_at = time.monotonic()
        normalized = str(query or "").strip()
        if not normalized:
            logger.debug("query_rejected reason=empty_query")
            raise TaskStateError("search query is required")
        logger.debug(
            "search_start discover=%s query=%r query_length=%d filters=%s",
            self.backend_name,
            normalized[:500],
            len(normalized),
            {"runtime_state": ["online"]},
        )
        if not self.available:
            logger.warning(
                "search_empty reason=discover_unavailable discover=%s elapsed_ms=%.1f",
                self.backend_name,
                (time.monotonic() - started_at) * 1000,
            )
            return {"candidates": [], "total": 0}
        result = await self.search(normalized)
        candidates = [
            self._project_candidate(item)
            for item in result.candidates
            if isinstance(item, dict) and (item.get("bot_uuid") or item.get("bot_id"))
        ][:20]
        logger.debug(
            "search_complete discover=%s query=%r tokens=%s raw_item_count=%d projected=%d "
            "failed_keywords=%s candidate_ids=%s elapsed_ms=%.1f",
            self.backend_name,
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
                self.backend_name,
                normalized[:500],
                result.tokens,
                result.raw_item_count,
                result.failed_keywords,
                (time.monotonic() - started_at) * 1000,
            )
        return {"candidates": candidates, "total": len(candidates)}
