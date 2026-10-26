"""Ranking contracts use opaque identities and never combine raw model scores."""

import pytest
from avernet_search.core.ranking import reciprocal_rank_fusion


def test_shared_hit_beats_single_source_and_ties_use_complete_identity():
    result = reciprocal_rank_fusion(
        [
            ["bot:owner:second", "shared"],
            ["bot:owner:first", "shared"],
        ]
    )
    assert [hit.item_id for hit in result] == [
        "shared",
        "bot:owner:first",
        "bot:owner:second",
    ]
    assert [hit.score for hit in result] == pytest.approx([2 / 62, 1 / 61, 1 / 61])


def test_duplicate_does_not_vote_twice_or_consume_a_rank():
    result = reciprocal_rank_fusion([["a", "a", "b"], ["b"]])
    assert [(hit.item_id, hit.score) for hit in result] == [
        ("b", 1 / 62 + 1 / 61),
        ("a", 1 / 61),
    ]


def test_empty_source_and_zero_results_are_valid():
    assert reciprocal_rank_fusion([[], []]) == []
    assert reciprocal_rank_fusion([[], ["agent"]])[0].score == 1 / 61


@pytest.mark.parametrize("rank_constant", [-1, 0])
def test_invalid_rank_constant_is_rejected(rank_constant):
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], rank_constant=rank_constant)


def test_empty_identity_is_rejected():
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[""]])
