"""Combine ranked identities without comparing unrelated raw score scales."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RankedIdentity:
    item_id: str
    score: float


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    rank_constant: int = 60,
) -> list[RankedIdentity]:
    """Each source votes once per identity; ties use the full opaque identity.

    Scores are raw reciprocal rank sums, not probabilities. Application-specific
    normalization, eligibility thresholds and pinned identities belong outside.
    Duplicate identities within one source do not consume additional ranks.
    """
    if rank_constant <= 0:
        raise ValueError("rank_constant must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(dict.fromkeys(ranking), 1):
            if not item_id:
                raise ValueError("item identity must not be empty")
            scores[item_id] = scores.get(item_id, 0.0) + 1 / (rank_constant + rank)
    return sorted(
        (RankedIdentity(key, value) for key, value in scores.items()),
        key=lambda item: (-item.score, item.item_id),
    )
