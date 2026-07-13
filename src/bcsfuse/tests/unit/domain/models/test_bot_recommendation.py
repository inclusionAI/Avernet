"""
BotRecommendation 单元测试
"""

import pytest

from src.domain.models.bot_recommendation import (
    BotRecommendationRequest,
    BotRecommendation,
    BotRecommendationResponse,
    create_bot_recommendation_response,
)
from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.retrieval_mode import RetrievalMode


class TestBotRecommendationRequest:
    """BotRecommendationRequest 测试"""

    def test_create_with_minimal_fields(self):
        """测试最小字段创建"""
        request = BotRecommendationRequest(question="测试问题")

        assert request.question == "测试问题"
        assert request.topK == 5
        assert request.driver_bot_id is None
        assert request.min_score == 0.01
        assert request.expand_factor == 10
        assert request.enable_rerank is True
        assert request.type == "recommend"  # 默认值

    def test_create_with_all_fields(self):
        """测试完整字段创建"""
        request = BotRecommendationRequest(
            question="测试问题",
            topK=10,
            driver_bot_id="wrk_test:default",
            min_score=0.5,
        )

        assert request.question == "测试问题"
        assert request.topK == 10
        assert request.driver_bot_id == "wrk_test:default"
        assert request.min_score == 0.5

    def test_topK_bounds(self):
        """测试 topK 边界"""
        # 最小值
        request = BotRecommendationRequest(question="test", topK=1)
        assert request.topK == 1

        # 最大值
        request = BotRecommendationRequest(question="test", topK=20)
        assert request.topK == 20

        # 超出边界应该失败
        with pytest.raises(Exception):
            BotRecommendationRequest(question="test", topK=0)

        with pytest.raises(Exception):
            BotRecommendationRequest(question="test", topK=21)

    def test_min_score_bounds(self):
        """测试 min_score 边界"""
        request = BotRecommendationRequest(question="test", min_score=0.0)
        assert request.min_score == 0.0

        request = BotRecommendationRequest(question="test", min_score=1.0)
        assert request.min_score == 1.0

        with pytest.raises(Exception):
            BotRecommendationRequest(question="test", min_score=-0.1)

        with pytest.raises(Exception):
            BotRecommendationRequest(question="test", min_score=1.1)

    def test_type_default_value(self):
        """type 字段默认值为 recommend"""
        request = BotRecommendationRequest(question="test")
        assert request.type == "recommend"

    def test_type_search_value(self):
        """type 字段支持 search 值"""
        request = BotRecommendationRequest(question="test", type="search")
        assert request.type == "search"

    def test_type_invalid_value(self):
        """type 字段只接受 search/recommend"""
        with pytest.raises(Exception):
            BotRecommendationRequest(question="test", type="invalid")


class TestBotRecommendation:
    """BotRecommendation 测试"""

    def test_create_bot_recommendation(self):
        """测试创建 BotRecommendation"""
        rec = BotRecommendation(
            profile_key="wrk_test:default",
            worker_id="wrk_test",
            score=0.8,
            reasons=["匹配技能: Python", "相关领域: 后端"],
        )

        assert rec.profile_key == "wrk_test:default"
        assert rec.score == 0.8
        assert len(rec.reasons) == 2
        assert rec.profile_tags == {}

    def test_create_bot_recommendation_with_profile_tags(self):
        """测试创建 BotRecommendation 带 profile_tags"""
        rec = BotRecommendation(
            profile_key="wrk_test:default",
            worker_id="wrk_test",
            score=0.8,
            reasons=[],
            profile_tags={"trust_level": "trusted"},
        )

        assert rec.profile_tags == {"trust_level": "trusted"}

    def test_create_bot_recommendation_with_multiple_profile_tags(self):
        """测试创建 BotRecommendation 带多个 profile_tags"""
        rec = BotRecommendation(
            profile_key="wrk_test:default",
            worker_id="wrk_test",
            score=0.8,
            reasons=[],
            profile_tags={"trust_level": "sandbox_only", "department": "platform"},
        )

        assert rec.profile_tags == {"trust_level": "sandbox_only", "department": "platform"}


class TestCreateBotRecommendationResponse:
    """create_bot_recommendation_response 测试"""

    def test_create_response(self):
        """测试创建响应"""
        candidate_response = CandidateRecommendationResponse(
            recommendations=[
                CandidateRecommendation(
                    profile_key="wrk_test_001:default",
                    worker_id="wrk_test_001",
                    score=0.8,
                    reasons=["Relevant skills: test"],
                    domain="test",
                    matched_skills=["test"],
                    is_supplement=True,
                ),
            ],
            question="测试问题",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=DomainCoverage(),
            participants_given=False,
            participants_sufficient=True,
            total_candidates=1,
            selected_candidates=1,
        )

        response = create_bot_recommendation_response(candidate_response)

        assert len(response.recommendations) == 1
        assert response.recommendations[0].profile_key == "wrk_test_001:default"
        assert response.recommendations[0].score == 0.8
        assert response.recommendations[0].reasons == ["Relevant skills: test"]
        assert response.driver_bot_id == "wrk_test_001:default"

    def test_create_response_with_driver_bot_id(self):
        """测试指定 driver_bot_id"""
        candidate_response = CandidateRecommendationResponse(
            recommendations=[
                CandidateRecommendation(
                    profile_key="wrk_test_001:default",
                    worker_id="wrk_test_001",
                    score=0.8,
                    reasons=[],
                    domain="test",
                    matched_skills=[],
                    is_supplement=True,
                ),
            ],
            question="测试问题",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=DomainCoverage(),
            participants_given=False,
            participants_sufficient=True,
            total_candidates=1,
            selected_candidates=1,
        )

        response = create_bot_recommendation_response(
            candidate_response,
            driver_bot_id="wrk_custom:default",
        )

        assert response.driver_bot_id == "wrk_custom:default"

    def test_create_response_empty_recommendations(self):
        """测试空推荐结果"""
        candidate_response = CandidateRecommendationResponse(
            recommendations=[],
            question="测试问题",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=DomainCoverage(),
            participants_given=False,
            participants_sufficient=False,
            total_candidates=0,
            selected_candidates=0,
        )

        response = create_bot_recommendation_response(candidate_response)

        assert response.driver_bot_id is None
        assert len(response.recommendations) == 0

    def test_create_response_with_trace_id(self):
        """测试 trace_id 传递"""
        candidate_response = CandidateRecommendationResponse(
            recommendations=[
                CandidateRecommendation(
                    profile_key="wrk_test_001:default",
                    worker_id="wrk_test_001",
                    score=0.8,
                    reasons=[],
                    domain="test",
                    matched_skills=[],
                    is_supplement=True,
                ),
            ],
            question="测试问题",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=DomainCoverage(),
            participants_given=False,
            participants_sufficient=True,
            total_candidates=1,
            selected_candidates=1,
        )

        response = create_bot_recommendation_response(
            candidate_response,
            trace_id="trace_1713945605000_a1b2c3d4",
            query_type="search",
        )

        assert response.trace_id == "trace_1713945605000_a1b2c3d4"
        assert response.type == "search"

    def test_create_response_default_trace_id(self):
        """测试不传 trace_id 时默认为空串"""
        candidate_response = CandidateRecommendationResponse(
            recommendations=[],
            question="测试问题",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=DomainCoverage(),
            participants_given=False,
            participants_sufficient=False,
            total_candidates=0,
            selected_candidates=0,
        )

        response = create_bot_recommendation_response(candidate_response)

        assert response.trace_id == ""
        assert response.type == "recommend"


class TestBotRecommendationResponse:
    """BotRecommendationResponse 测试"""

    def test_create_with_recommendations(self):
        """测试创建响应"""
        response = BotRecommendationResponse(
            trace_id="trace_123_abc",
            driver_bot_id="wrk_driver:default",
            recommendations=[
                BotRecommendation(
                    profile_key="wrk_high:default",
                    worker_id="wrk_high",
                    score=0.9,
                    reasons=["高匹配度"],
                ),
                BotRecommendation(
                    profile_key="wrk_low:default",
                    worker_id="wrk_low",
                    score=0.3,
                    reasons=["低匹配度"],
                ),
            ],
        )

        assert response.trace_id == "trace_123_abc"
        assert response.type == "recommend"  # 默认值
        assert response.driver_bot_id == "wrk_driver:default"
        assert len(response.recommendations) == 2
        assert response.recommendations[0].profile_key == "wrk_high:default"
        assert response.recommendations[0].score == 0.9

    def test_trace_id_required(self):
        """trace_id 是必填字段"""
        with pytest.raises(Exception):
            BotRecommendationResponse(
                driver_bot_id="wrk:default",
                recommendations=[],
            )

    def test_type_search(self):
        """type 字段支持 search 值"""
        response = BotRecommendationResponse(
            trace_id="trace_test",
            type="search",
            recommendations=[],
        )
        assert response.type == "search"

    def test_type_default_recommend(self):
        """type 字段默认值为 recommend"""
        response = BotRecommendationResponse(
            trace_id="trace_test",
            recommendations=[],
        )
        assert response.type == "recommend"

    def test_type_invalid_value(self):
        """type 字段只接受 search/recommend"""
        with pytest.raises(Exception):
            BotRecommendationResponse(
                trace_id="trace_test",
                type="invalid",
                recommendations=[],
            )