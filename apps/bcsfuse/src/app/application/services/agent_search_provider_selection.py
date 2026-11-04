"""Select complementary online providers without leaking fallback policy."""

from __future__ import annotations

import inspect
from typing import Any


def select_online_embedding_provider(registry: Any, fallback: Any) -> Any:
    """Return the native async online provider, or the known sync fallback."""

    provider = registry.get("agent_online_embedding_provider")
    embed = getattr(provider, "embed", None)
    if callable(embed) and inspect.iscoroutinefunction(embed):
        return provider
    return fallback


__all__ = ["select_online_embedding_provider"]
