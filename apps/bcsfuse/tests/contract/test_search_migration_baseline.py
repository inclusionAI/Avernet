"""TC caller-visible identities and score semantics survive Core extraction."""

from app.adapters.http.schemas.recommend_schemas import BotRecommendationRequest


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
