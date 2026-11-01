"""Bounded dense payload projection for fragment retrieval."""

from __future__ import annotations

from typing import Any

# Full `content` is intentionally omitted; rerank loads complete profile text
# for selected candidates instead.
DENSE_FRAGMENT_PAYLOAD_FIELDS = (
    "profile_key",
    "worker_id",
    "profile_id",
    "profile_type",
    "fragment_type",
    "domains",
    "active_skills",
    "short_profile",
    "display_name",
    "content_preview",
    "_external_id",
)


def dense_fragment_search(
    vector_store: Any,
    query_embedding: list[float],
    top_k: int,
    filters: dict[str, Any] | None,
    *,
    supports_payload_projection: bool,
) -> list[Any]:
    """Search with a bounded payload when the provider supports projection."""

    kwargs: dict[str, Any] = {}
    if supports_payload_projection:
        kwargs["payload_fields"] = list(DENSE_FRAGMENT_PAYLOAD_FIELDS)
    return vector_store.search(query_embedding, top_k, filters=filters, **kwargs)


__all__ = [
    "DENSE_FRAGMENT_PAYLOAD_FIELDS",
    "dense_fragment_search",
]
