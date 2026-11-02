"""Bounded execution policies for the public v1 search mode contract."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchModePolicy:
    """Bounded execution policy selected by an explicit v1 search mode."""

    requested_mode: str
    retrieval_mode: str
    rerank_enabled: bool
    expand_factor: int | None


# ``auto`` inherits the composed runtime policy. The other modes are explicit,
# bounded contracts and cannot silently change through arbitrary strings.
_SEARCH_MODE_POLICIES: dict[str, SearchModePolicy] = {
    "auto": SearchModePolicy("auto", "auto", False, None),
    "fast": SearchModePolicy("fast", "fragment", False, 1),
    "balanced": SearchModePolicy("balanced", "fragment", True, 2),
    "accurate": SearchModePolicy("accurate", "fragment", True, 3),
}


def resolve_search_mode_policy(mode: str) -> SearchModePolicy:
    """Resolve a validated mode into its bounded application policy."""

    policy = _SEARCH_MODE_POLICIES.get(mode)
    if policy is None:
        raise ValueError(f"unsupported search mode: {mode}")
    return policy


__all__ = ["SearchModePolicy", "resolve_search_mode_policy"]
