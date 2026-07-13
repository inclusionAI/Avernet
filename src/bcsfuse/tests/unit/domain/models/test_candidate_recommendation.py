"""
CandidateRecommendation Model Unit Tests

Stage 4: G5 real-context deepening / candidate recommendation 正式接入
"""

from __future__ import annotations

import pytest

from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.retrieval_mode import RetrievalMode


class TestCandidateRecommendationModel:
    """CandidateRecommendation 模型测试"""

    def test_candidate_recommendation_minimal(self):
        """测试最小字段"""
        rec = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.8,
        )

        assert rec.profile_key == "staff_001:default"
        assert rec.score == 0.8
        assert rec.reasons == []
        assert rec.domain == "general"
        assert rec.domain_confidence == 0.5
        assert rec.matched_skills == []
        assert rec.matched_contexts == []
        assert rec.is_supplement is False

    def test_candidate_recommendation_all_fields(self):
        """测试所有字段"""
        rec = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.85,
            reasons=["Skill match: Security", "Context match: AGENTS.md"],
            domain="security",
            domain_confidence=0.9,
            matched_skills=["Security", "Architecture"],
            matched_contexts=["AGENTS.md", "SOUL.md"],
            is_supplement=True,
        )

        assert rec.profile_key == "staff_001:default"
        assert rec.score == 0.85
        assert rec.reasons == ["Skill match: Security", "Context match: AGENTS.md"]
        assert rec.domain == "security"
        assert rec.domain_confidence == 0.9
        assert rec.matched_skills == ["Security", "Architecture"]
        assert rec.matched_contexts == ["AGENTS.md", "SOUL.md"]
        assert rec.is_supplement is True

    def test_is_supplement_flag(self):
        """测试 is_supplement 标记"""
        # 显式 participant
        explicit = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.9,
            is_supplement=False,
        )
        assert explicit.is_supplement is False

        # 补充推荐
        supplement = CandidateRecommendation(
            profile_key="staff_002:default",
            worker_id="staff_002",
            score=0.75,
            is_supplement=True,
        )
        assert supplement.is_supplement is True

    def test_matched_skills_list(self):
        """测试匹配技能列表"""
        rec = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.8,
            matched_skills=["Security", "Database", "Architecture"],
        )

        assert rec.matched_skills == ["Security", "Database", "Architecture"]
        assert len(rec.matched_skills) == 3

    def test_is_high_confidence(self):
        """测试高置信度判断"""
        # 高置信度
        high = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.8,
            domain_confidence=0.7,
        )
        assert high.is_high_confidence is True

        # 分数不够
        low_score = CandidateRecommendation(
            profile_key="staff_002:default",
            worker_id="staff_002",
            score=0.6,
            domain_confidence=0.8,
        )
        assert low_score.is_high_confidence is False

        # 领域置信度不够
        low_domain = CandidateRecommendation(
            profile_key="staff_003:default",
            worker_id="staff_003",
            score=0.8,
            domain_confidence=0.4,
        )
        assert low_domain.is_high_confidence is False

    def test_is_low_confidence(self):
        """测试低置信度判断"""
        low = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.3,
        )
        assert low.is_low_confidence is True

        high = CandidateRecommendation(
            profile_key="staff_002:default",
            worker_id="staff_002",
            score=0.6,
        )
        assert high.is_low_confidence is False

    def test_score_bounds(self):
        """测试分数边界"""
        # 最小值
        min_score = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.0,
        )
        assert min_score.score == 0.0

        # 最大值
        max_score = CandidateRecommendation(
            profile_key="staff_002:default",
            worker_id="staff_002",
            score=1.0,
        )
        assert max_score.score == 1.0

        # 超出边界应失败
        with pytest.raises(Exception):
            CandidateRecommendation(
                profile_key="staff_003:default",
                worker_id="staff_003",
                score=-0.1,
            )

        with pytest.raises(Exception):
            CandidateRecommendation(
                profile_key="staff_004:default",
                worker_id="staff_004",
                score=1.1,
            )


class TestCandidateRecommendationResponse:
    """CandidateRecommendationResponse 模型测试"""

    def test_response_minimal(self):
        """测试最小响应"""
        response = CandidateRecommendationResponse(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
        )

        assert response.question == "How to secure the API?"
        assert response.mode == RetrievalMode.EXPERT_DIAGNOSIS
        assert response.recommendations == []
        assert response.participants_given is False
        assert response.participants_sufficient is False
        assert response.min_experts == 3

    def test_response_with_recommendations(self):
        """测试带推荐的响应"""
        rec1 = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.9,
            is_supplement=False,
        )
        rec2 = CandidateRecommendation(
            profile_key="staff_002:default",
            worker_id="staff_002",
            score=0.8,
            is_supplement=True,
        )

        response = CandidateRecommendationResponse(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            recommendations=[rec1, rec2],
            participants_given=True,
            participants_sufficient=True,
        )

        assert len(response.recommendations) == 2
        assert response.recommendations[0].profile_key == "staff_001:default"
        assert response.recommendations[1].profile_key == "staff_002:default"

    def test_explicit_vs_supplement(self):
        """测试显式与补充推荐分离"""
        explicit1 = CandidateRecommendation(
            profile_key="staff_001:default",
            worker_id="staff_001",
            score=0.9,
            is_supplement=False,
        )
        explicit2 = CandidateRecommendation(
            profile_key="staff_002:default",
            worker_id="staff_002",
            score=0.85,
            is_supplement=False,
        )
        supplement1 = CandidateRecommendation(
            profile_key="staff_003:default",
            worker_id="staff_003",
            score=0.75,
            is_supplement=True,
        )

        response = CandidateRecommendationResponse(
            question="Test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            recommendations=[explicit1, explicit2, supplement1],
            participants_given=True,
        )

        # 显式 participants
        explicit = response.explicit_participants
        assert len(explicit) == 2
        assert all(r.is_supplement is False for r in explicit)

        # 补充推荐
        supplements = response.supplement_candidates
        assert len(supplements) == 1
        assert all(r.is_supplement is True for r in supplements)

    def test_recommendation_order(self):
        """测试推荐顺序：显式在前，补充在后"""
        response = CandidateRecommendationResponse(
            question="Test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            recommendations=[
                CandidateRecommendation(profile_key="explicit_1", worker_id="worker_001", score=0.9, is_supplement=False),
                CandidateRecommendation(profile_key="explicit_2", worker_id="worker_002", score=0.85, is_supplement=False),
                CandidateRecommendation(profile_key="supplement_1", worker_id="worker_003", score=0.8, is_supplement=True),
                CandidateRecommendation(profile_key="supplement_2", worker_id="worker_004", score=0.75, is_supplement=True),
            ],
        )

        # 验证顺序
        assert response.recommendations[0].profile_key == "explicit_1"
        assert response.recommendations[1].profile_key == "explicit_2"
        assert response.recommendations[2].profile_key == "supplement_1"
        assert response.recommendations[3].profile_key == "supplement_2"

    def test_domain_coverage_in_response(self):
        """测试响应中的领域覆盖"""
        coverage = DomainCoverage(
            required_domains=["security", "legal"],
            covered_domains=["security"],
            missing_domains=["legal"],
            coverage_score=0.5,
        )

        response = CandidateRecommendationResponse(
            question="Test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            domain_coverage=coverage,
        )

        assert response.domain_coverage.required_domains == ["security", "legal"]
        assert response.domain_coverage.missing_domains == ["legal"]

    def test_needs_more_candidates(self):
        """测试是否需要更多候选人"""
        # 需要更多
        response1 = CandidateRecommendationResponse(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            selected_candidates=2,
            min_experts=3,
        )
        assert response1.needs_more_candidates is True

        # 已足够
        response2 = CandidateRecommendationResponse(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            selected_candidates=3,
            min_experts=3,
        )
        assert response2.needs_more_candidates is False

    def test_high_confidence_count(self):
        """测试高置信度推荐数量"""
        response = CandidateRecommendationResponse(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            recommendations=[
                CandidateRecommendation(profile_key="p1", worker_id="worker_001", score=0.9, domain_confidence=0.8),
                CandidateRecommendation(profile_key="p2", worker_id="worker_002", score=0.6, domain_confidence=0.8),  # 低分
                CandidateRecommendation(profile_key="p3", worker_id="worker_003", score=0.8, domain_confidence=0.3),  # 低领域置信度
                CandidateRecommendation(profile_key="p4", worker_id="worker_004", score=0.85, domain_confidence=0.7),
            ],
        )

        assert response.high_confidence_count == 2  # p1 和 p4

    def test_participants_sufficient_flag(self):
        """测试 participants_sufficient 标记"""
        response1 = CandidateRecommendationResponse(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants_sufficient=True,
        )
        assert response1.participants_sufficient is True

        response2 = CandidateRecommendationResponse(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants_sufficient=False,
        )
        assert response2.participants_sufficient is False

    def test_model_dump(self):
        """测试模型序列化"""
        response = CandidateRecommendationResponse(
            question="Test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants_given=True,
            participants_sufficient=True,
        )

        data = response.model_dump()
        assert "question" in data
        assert "mode" in data
        assert "participants_given" in data
        assert "participants_sufficient" in data

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):
            CandidateRecommendation(
                profile_key="staff_001:default",
                worker_id="staff_001",
                score=0.8,
                unknown_field="not_allowed",
            )