"""Select a bounded union of raw-max and existing aggregate-score candidates."""

import logging

from src.application.services.worker_vector_match_types import FragmentProfileCandidate
from src.domain.services.retrieval_logging import log_rows

logger = logging.getLogger("src.application.services.worker_vector_match_service")


def select_rerank_candidates(
    candidates: list[FragmentProfileCandidate], budget: int,
) -> tuple[list[FragmentProfileCandidate], dict]:
    """Take both half-budget heads, deduplicate, then alternate unique refills.

    Input is already grouped by profile_key and eligibility-filtered. Return
    weighted order so existing no-provider/error fallbacks do not use max rank
    as their final ranking. Odd budgets give the extra initial slot to raw max.
    """
    max_scores = {
        c.profile_key: max((f.score for f in c.fragments), default=0.0)
        for c in candidates
    }
    weighted = sorted(candidates, key=lambda c: (-c.aggregated_score, c.profile_key))
    by_max = sorted(candidates, key=lambda c: (-max_scores[c.profile_key], c.profile_key))
    max_quota, weighted_quota = (budget + 1) // 2, budget // 2
    max_head = {c.profile_key for c in by_max[:max_quota]}
    weighted_head = {c.profile_key for c in weighted[:weighted_quota]}
    selected = max_head | weighted_head
    decisions = {
        key: "both_heads" if key in max_head & weighted_head
        else "max_head" if key in max_head else "weighted_head"
        for key in selected
    }
    initial_count = len(selected)
    tails = [(iter(by_max[max_quota:]), "max_refill"),
             (iter(weighted[weighted_quota:]), "weighted_refill")]
    while len(selected) < min(budget, len(candidates)):
        for tail, reason in tails:
            for candidate in tail:
                if candidate.profile_key not in selected:
                    selected.add(candidate.profile_key)
                    decisions[candidate.profile_key] = reason
                    break
            if len(selected) >= min(budget, len(candidates)):
                break

    max_ranks = {c.profile_key: i for i, c in enumerate(by_max, 1)}
    weighted_ranks = {c.profile_key: i for i, c in enumerate(weighted, 1)}
    log_rows(logger, "candidate_decisions", [
        "profile_key", "max_score", "weighted_score", "max_rank", "weighted_rank",
        "decision", "hit_types",
    ], (
        [c.profile_key, round(max_scores[c.profile_key], 6), round(c.aggregated_score, 6),
         max_ranks[c.profile_key], weighted_ranks[c.profile_key],
         decisions.get(c.profile_key, "budget_rejected"),
         sorted({f.fragment_type for f in c.fragments})]
        for c in weighted
    ))
    stats = {
        "selection_strategy": "raw_max_weighted_union",
        "rerank_budget": budget,
        "max_head_count": len(max_head),
        "weighted_head_count": len(weighted_head),
        "head_overlap_count": len(max_head & weighted_head),
        "refill_count": len(selected) - initial_count,
        "budget_rejected_count": len(candidates) - len(selected),
    }
    return [c for c in weighted if c.profile_key in selected], stats
