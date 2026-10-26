"""TC caller-visible identities and score semantics survive Core extraction."""

import pytest
from src.application.services.keyword_candidate_selection import fuse_candidates
from src.application.services.worker_vector_match_types import FragmentProfileCandidate
from src.domain.models.profile_fragment import FragmentMatch
from src.interfaces.api.schemas.recommend_schemas import BotRecommendationRequest


def candidate(key, score, fragment, **metadata):
    return FragmentProfileCandidate(
        key,
        score,
        [FragmentMatch("full", fragment, score, fragment)],
        metadata,
    )


def test_tc_union_keeps_profiles_fragments_exact_priority_and_threshold():
    dense = [candidate("bot:owner:one", 0.8, "dense"), candidate("below", 0.001, "low")]
    lexical = [
        candidate("bot:owner:two", 0.0, "exact", _keyword_exact_id=True),
        candidate("bot:owner:one", 0.0, "lexical"),
    ]
    result = fuse_candidates(dense, lexical, 5, 0.01)
    assert [c.profile_key for c in result] == ["bot:owner:two", "bot:owner:one"]
    assert result[0].aggregated_score == 1.0
    assert result[1].aggregated_score == pytest.approx(
        0.99 * 61 / 2 * (1 / 61 + 1 / 62)
    )
    assert [f.fragment_id for f in result[1].fragments] == ["dense", "lexical"]
    assert [f.fragment_id for f in dense[0].fragments] == ["dense"]


def test_bcn_and_backend_request_shapes_keep_existing_defaults():
    bcn = BotRecommendationRequest(question="数据分析", topK=10, min_score=0.01)
    assert bcn.expand_factor == 10
    assert bcn.enable_rerank is True
    assert bcn.type == "recommend"
    assert "min_score" in bcn.model_fields_set
    implicit = BotRecommendationRequest(question="数据分析")
    assert "min_score" not in implicit.model_fields_set
    backend = BotRecommendationRequest(
        question="数据分析",
        topK=10,
        min_score=0.01,
        expand_factor=10,
        enable_rerank=True,
        filters={"runtime_state": ["online"]},
    )
    assert backend.filters == {"runtime_state": ["online"]}
