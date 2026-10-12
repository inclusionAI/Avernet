"""Keep the published score model compatible after retiring its old service."""

import pytest

from src.domain.models.hybrid_score import (
    DenseScore,
    HybridScore,
    ScoreSource,
    SparseScore,
)


def test_hybrid_score_factory_preserves_normalized_weighting():
    result = HybridScore.from_hybrid(
        DenseScore(similarity=0.9), SparseScore(bm25_score=15), alpha=0.6
    )
    assert result.final_score == pytest.approx(0.87)
    assert result.source == ScoreSource.HYBRID


def test_dense_only_factory_preserves_score():
    result = HybridScore.from_dense_only(DenseScore(similarity=0.9))
    assert result.final_score == pytest.approx(0.95)
    assert result.source == ScoreSource.DENSE


def test_sparse_only_factory_preserves_score():
    result = HybridScore.from_sparse_only(SparseScore(bm25_score=15))
    assert result.final_score == pytest.approx(0.75)
    assert result.source == ScoreSource.SPARSE


def test_breakdown_retains_model_version_and_matched_terms():
    result = HybridScore.from_hybrid(
        DenseScore(similarity=0.9, model_version="v1"),
        SparseScore(bm25_score=15, matched_terms=["test"]),
        alpha=0.6,
    )
    breakdown = result.to_breakdown_dict()
    assert breakdown["final_score"] == pytest.approx(0.87)
    assert breakdown["source"] == "hybrid"
    assert breakdown["dense"]["model_version"] == "v1"
    assert breakdown["sparse"]["matched_terms"] == ["test"]
