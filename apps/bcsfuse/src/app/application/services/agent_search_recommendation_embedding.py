"""Recommendation embedding stage shared by the application service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.application.services.agent_search_embedding_executor import (
    execute_embedding_query,
)
from app.application.services.semantic_failure_metadata import (
    semantic_failure_metadata,
)
from app.infra.public.observability.search_stage_metrics import (
    search_stage_metrics,
)

Offload = Callable[[Callable[[], Any]], Awaitable[Any]]


async def prepare_recommendation_query_embedding(
    *,
    provider: Any,
    question: str,
    timeout_seconds: float | None,
    offload: Offload,
) -> tuple[list[float], dict[str, Any] | None]:
    """Embed once and return sanitized failure metadata on degradation."""

    try:
        with search_stage_metrics("recommend", "embedding"):
            vector = await execute_embedding_query(
                provider,
                question,
                timeout_seconds,
                offload,
            )
            return vector, None
    except Exception as error:
        return [], semantic_failure_metadata(error)


__all__ = ["prepare_recommendation_query_embedding"]
