"""Select a bounded union of two ranked lists without business score scales."""

from collections.abc import Sequence


def select_rank_union(
    primary: Sequence[str],
    secondary: Sequence[str],
    *,
    budget: int,
) -> dict[str, str]:
    """Split the initial budget, then alternate unique refills.

    The odd initial slot belongs to primary. Returned reasons explain admission,
    not final order; the application chooses the final score/ranking policy.
    """
    if budget < 0:
        raise ValueError("budget must not be negative")
    primary, secondary = list(dict.fromkeys(primary)), list(dict.fromkeys(secondary))
    primary_quota, secondary_quota = (budget + 1) // 2, budget // 2
    primary_head = set(primary[:primary_quota])
    secondary_head = set(secondary[:secondary_quota])
    chosen = {
        key: "both_heads"
        if key in primary_head & secondary_head
        else "primary_head"
        if key in primary_head
        else "secondary_head"
        for key in primary_head | secondary_head
    }
    target = min(budget, len(set(primary) | set(secondary)))
    tails = [
        (iter(primary[primary_quota:]), "primary_refill"),
        (iter(secondary[secondary_quota:]), "secondary_refill"),
    ]
    while len(chosen) < target:
        for tail, reason in tails:
            for key in tail:
                if key not in chosen:
                    chosen[key] = reason
                    break
            if len(chosen) >= target:
                break
    return chosen
