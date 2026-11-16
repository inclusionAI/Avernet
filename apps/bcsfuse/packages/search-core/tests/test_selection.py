"""Ranking heads share a budget without collapsing opaque identities."""

import pytest
from search_core.core.selection import select_rank_union


def test_overlapping_heads_refill_and_preserve_profile_identity():
    chosen = select_rank_union(
        ["bot:owner:p1", "bot:owner:p2", "b", "c"],
        ["bot:owner:p1", "bot:owner:p2", "c", "b"],
        budget=4,
    )
    assert chosen == {
        "bot:owner:p1": "both_heads",
        "bot:owner:p2": "both_heads",
        "b": "primary_refill",
        "c": "secondary_refill",
    }


def test_odd_budget_and_duplicate_votes():
    chosen = select_rank_union(["a", "a", "b", "c"], ["c", "b", "a"], budget=3)
    assert chosen == {"a": "primary_head", "b": "primary_head", "c": "secondary_head"}
    assert select_rank_union([], ["a"], budget=5) == {"a": "secondary_head"}
    assert select_rank_union(["a"], ["b"], budget=0) == {}


def test_negative_budget_rejected():
    with pytest.raises(ValueError):
        select_rank_union([], [], budget=-1)
