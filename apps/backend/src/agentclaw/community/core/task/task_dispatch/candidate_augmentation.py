"""Pluggable augmentation of Relay search candidates.

Augmentation is server-owned so the same candidate list can be returned to the
Skill and recorded as the authoritative Relay search receipt.  The temporary duty fallback
implementation is deliberately isolated: deployments can turn it off by leaving
``task_dispatch.temporary_duty_bot_candidates`` empty and remove it by deleting
the provider wiring without changing the Search report contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.domain.models import BotCandidate

SEARCH_CANDIDATE_SOURCE = "search"
SEARCH_PLUS_FALLBACK_CANDIDATE_SOURCE = "search_plus_fallback"


@dataclass(frozen=True, kw_only=True)
class SearchCandidateReceipt:
    """Augmented candidates plus receipt-only diagnostics."""

    candidates: tuple[BotCandidate, ...]
    candidate_source: str
    fallback_candidate_count: int


class SearchCandidateAugmentor(Protocol):
    """Augment an already successful server search result."""

    def augment(self, candidates: Sequence[BotCandidate]) -> SearchCandidateReceipt:
        """Return the final candidate pool and its source diagnostics."""
        ...


class NullSearchCandidateAugmentor:
    """Keep the effective public search contract unchanged."""

    def augment(self, candidates: Sequence[BotCandidate]) -> SearchCandidateReceipt:
        return SearchCandidateReceipt(
            candidates=tuple(candidates),
            candidate_source=SEARCH_CANDIDATE_SOURCE,
            fallback_candidate_count=0,
        )


class TemporaryDutySearchCandidateAugmentor:
    """Add configured duty-domain candidates only when search has no overlap.

    This is a temporary pluggable implementation.  It takes no bot supplied by
    the request and adds no score; S5D therefore still chooses from an
    unscored, server-defined fallback only after a successful catalog search.
    """

    def __init__(self, fallback_candidates: Sequence[BotCandidate]) -> None:
        fallbacks: dict[str, BotCandidate] = {}
        for candidate in fallback_candidates:
            normalized = str(candidate.bot_id).strip()
            if not normalized:
                raise ValueError("temporary duty fallback candidate requires bot_id")
            if normalized in fallbacks:
                raise ValueError(f"duplicate temporary duty candidate bot_id: {normalized}")
            fallbacks[normalized] = BotCandidate(
                bot_id=normalized,
                name=str(candidate.name or normalized),
                description=str(candidate.description or ""),
                score=None,
            )
        self._fallback_candidates = tuple(fallbacks.values())

    def augment(self, candidates: Sequence[BotCandidate]) -> SearchCandidateReceipt:
        search_candidates = tuple(candidates)
        search_ids = {candidate.bot_id for candidate in search_candidates}
        fallback_ids = {candidate.bot_id for candidate in self._fallback_candidates}
        if not search_ids.isdisjoint(fallback_ids):
            return SearchCandidateReceipt(
                candidates=search_candidates,
                candidate_source=SEARCH_CANDIDATE_SOURCE,
                fallback_candidate_count=0,
            )
        additions = tuple(
            candidate
            for candidate in self._fallback_candidates
            if candidate.bot_id not in search_ids
        )
        return SearchCandidateReceipt(
            candidates=(*search_candidates, *additions),
            candidate_source=SEARCH_PLUS_FALLBACK_CANDIDATE_SOURCE,
            fallback_candidate_count=len(additions),
        )
