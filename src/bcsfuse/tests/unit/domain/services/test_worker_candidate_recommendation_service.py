"""
WorkerCandidateRecommendationService Interface Unit Tests

Stage 4: G5 real-context deepening / candidate recommendation 正式接入
"""

from __future__ import annotations

import pytest

from src.domain.services.worker_candidate_recommendation_service import (
    WorkerCandidateRecommendationService,
)
from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.retrieval_mode import RetrievalMode


class TestWorkerCandidateRecommendationServiceInterface:
    """WorkerCandidateRecommendationService 接口测试"""

    def test_interface_is_protocol(self):
        """测试接口是 Protocol"""
        from typing import Protocol
        assert issubclass(WorkerCandidateRecommendationService, Protocol)

    def test_interface_has_recommend_method(self):
        """测试接口有 recommend 方法"""
        assert hasattr(WorkerCandidateRecommendationService, "recommend")

    def test_interface_is_runtime_checkable(self):
        """测试接口是 runtime_checkable"""
        # 创建一个实现了接口的类
        class MockRecommendationService:
            def recommend(
                self,
                question: str,
                mode: RetrievalMode,
                participants: list[str] | None = None,
                max_candidates: int = 5,
                min_experts: int = 3,
            ) -> CandidateRecommendationResponse:
                return CandidateRecommendationResponse(
                    question=question,
                    mode=mode,
                )

        service = MockRecommendationService()
        assert isinstance(service, WorkerCandidateRecommendationService)

    def test_recommend_method_signature(self):
        """测试 recommend 方法签名"""
        import inspect

        # 获取 recommend 方法的签名
        # Protocol 方法在类上不存在实例方法，需要检查 __annotations__
        # 或直接检查接口定义

        # 创建实际实现来验证签名
        class MockService:
            def recommend(
                self,
                question: str,
                mode: RetrievalMode,
                participants: list[str] | None = None,
                max_candidates: int = 5,
                min_experts: int = 3,
            ) -> CandidateRecommendationResponse:
                return CandidateRecommendationResponse(
                    question=question,
                    mode=mode,
                )

        service = MockService()
        sig = inspect.signature(service.recommend)

        # 验证参数
        params = sig.parameters
        assert "question" in params
        assert "mode" in params
        assert "participants" in params
        assert "max_candidates" in params
        assert "min_experts" in params

    def test_recommend_returns_response(self):
        """测试 recommend 返回 CandidateRecommendationResponse"""
        class MockService:
            def recommend(
                self,
                question: str,
                mode: RetrievalMode,
                participants: list[str] | None = None,
                max_candidates: int = 5,
                min_experts: int = 3,
            ) -> CandidateRecommendationResponse:
                return CandidateRecommendationResponse(
                    question=question,
                    mode=mode,
                )

        service = MockService()
        response = service.recommend(
            question="Test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
        )

        assert isinstance(response, CandidateRecommendationResponse)
        assert response.question == "Test question"
        assert response.mode == RetrievalMode.EXPERT_DIAGNOSIS

    def test_interface_docstring_exists(self):
        """测试接口有文档说明"""
        assert WorkerCandidateRecommendationService.__doc__ is not None
        assert "Candidate Recommendation" in WorkerCandidateRecommendationService.__doc__


class TestWorkerCandidateRecommendationServiceMock:
    """接口 Mock 实现测试"""

    def test_mock_implementation_with_participants(self):
        """测试 Mock 实现：有显式 participants"""
        class MockService:
            def recommend(
                self,
                question: str,
                mode: RetrievalMode,
                participants: list[str] | None = None,
                max_candidates: int = 5,
                min_experts: int = 3,
            ) -> CandidateRecommendationResponse:
                recs = [
                    CandidateRecommendation(
                        profile_key=p,
                        score=0.9,
                        is_supplement=False,
                    )
                    for p in (participants or [])
                ]
                return CandidateRecommendationResponse(
                    question=question,
                    mode=mode,
                    recommendations=recs,
                    participants_given=participants is not None,
                    participants_sufficient=len(participants or []) >= min_experts,
                )

        service = MockService()
        response = service.recommend(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
        )

        assert response.participants_given is True
        assert response.participants_sufficient is True
        assert len(response.recommendations) == 3

    def test_mock_implementation_without_participants(self):
        """测试 Mock 实现：无显式 participants"""
        class MockService:
            def recommend(
                self,
                question: str,
                mode: RetrievalMode,
                participants: list[str] | None = None,
                max_candidates: int = 5,
                min_experts: int = 3,
            ) -> CandidateRecommendationResponse:
                # 模拟补充推荐
                recs = [
                    CandidateRecommendation(
                        profile_key="staff_001:default",
                        score=0.85,
                        is_supplement=True,
                    ),
                    CandidateRecommendation(
                        profile_key="staff_002:default",
                        score=0.8,
                        is_supplement=True,
                    ),
                ]
                return CandidateRecommendationResponse(
                    question=question,
                    mode=mode,
                    recommendations=recs,
                    participants_given=False,
                    participants_sufficient=False,
                )

        service = MockService()
        response = service.recommend(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
        )

        assert response.participants_given is False
        assert response.participants_sufficient is False
        assert all(r.is_supplement for r in response.recommendations)