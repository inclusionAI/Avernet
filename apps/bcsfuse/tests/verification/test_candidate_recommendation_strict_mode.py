"""
Candidate Recommendation Service strict_participants测试

这些测试验证候选推荐服务在 strict 模式下的行为。

关键验证点：
1. strict=True 时，禁止补充推荐
2. strict=True 时，只返回能找到的显式 participants
3. strict=False 时，允许补充推荐
"""

import pytest
from unittest.mock import MagicMock
from src.application.services.worker_candidate_recommendation_impl import WorkerCandidateRecommendationImpl
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.worker_profile import WorkerProfile, ProfileType, SourceType
from src.domain.models.skill_profile import SkillProfile
from src.domain.services.worker_profile_retrieval_service import RetrievalResponse, RetrievalResult


def create_mock_profile(staff_id: str, profile_id: str = "default") -> WorkerProfile:
    """创建模拟 Profile"""
    profile = WorkerProfile(
        staff_id=staff_id,
        profile_id=profile_id,
        profile_type=ProfileType.DEFAULT,
        source_type=SourceType.FILE,
        source_root='/test',
        active_skills=[SkillProfile(skill_set_name='test', skill_id='s1', name='test', description='test')],
        context_fragments=[],
    )
    profile._searchable_text = 'test'
    return profile


class TestCandidateRecommendationStrictMode:
    """候选推荐服务 strict 模式测试"""

    def test_strict_mode_blocks_supplement_recommendations(self):
        """
        验证：strict=True 时，禁止补充推荐

        场景：
        - 用户请求不存在的 participant: ["wrk_nonexistent:default"]
        - strict=True
        - 应该返回空列表，不补充其他专家
        """
        mock_retrieval = MagicMock()
        # 显式 participant 找不到
        mock_retrieval.retrieve.return_value = RetrievalResponse(
            results=[],
            question='',
            mode=RetrievalMode.EXPERT_DIAGNOSIS
        )

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval,
            min_experts=3,
        )

        result = service.recommend(
            question='test question',
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=['wrk_nonexistent:default'],
            strict_participants=True,
        )

        # 应该返回空
        assert len(result.recommendations) == 0
        assert result.participants_given == True
        # 不应该调用第二次检索（补充推荐）
        assert mock_retrieval.retrieve.call_count == 1

    def test_non_strict_mode_allows_supplement_recommendations(self):
        """
        验证：strict=False 时，允许补充推荐

        场景：
        - 用户请求不存在的 participant: ["wrk_nonexistent:default"]
        - strict=False
        - 应该返回补充推荐
        """
        mock_retrieval = MagicMock()
        existing_profile = create_mock_profile('wrk_other')

        # 第一次返回空（显式找不到），第二次返回补充
        mock_retrieval.retrieve.side_effect = [
            RetrievalResponse(results=[], question='', mode=RetrievalMode.EXPERT_DIAGNOSIS),
            RetrievalResponse(
                results=[RetrievalResult(profile=existing_profile, total_score=0.8)],
                question='',
                mode=RetrievalMode.EXPERT_DIAGNOSIS
            ),
        ]

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval,
            min_experts=3,
        )

        result = service.recommend(
            question='test question',
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=['wrk_nonexistent:default'],
            strict_participants=False,
        )

        # 应该返回补充推荐
        assert len(result.recommendations) == 1
        assert result.recommendations[0].is_supplement == True
        # 应该调用两次（显式 + 补充）
        assert mock_retrieval.retrieve.call_count == 2

    def test_strict_mode_returns_found_explicit_participants_only(self):
        """
        验证：strict=True 时，只返回找到的显式 participants

        场景：
        - 用户请求多个 participants
        - 部分存在，部分不存在
        - strict=True
        - 应该只返回找到的部分，不补充
        """
        mock_retrieval = MagicMock()
        existing_profile = create_mock_profile('wrk_existing')

        # 只有一个 participant 能找到
        mock_retrieval.retrieve.return_value = RetrievalResponse(
            results=[RetrievalResult(profile=existing_profile, total_score=0.9)],
            question='',
            mode=RetrievalMode.EXPERT_DIAGNOSIS
        )

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval,
            min_experts=3,
        )

        result = service.recommend(
            question='test question',
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=['wrk_existing:default', 'wrk_nonexistent:default'],
            strict_participants=True,
        )

        # 应该只返回找到的
        assert len(result.recommendations) == 1
        assert result.recommendations[0].profile_key == 'staff_wrk_existing:default'
        assert result.recommendations[0].is_supplement == False

    def test_strict_mode_with_no_participants_allows_full_db_search(self):
        """
        验证：strict=True 但 participants=None 时，允许全库检索

        场景：
        - 用户没有指定 participants
        - strict=True（无意义，因为用户没有指定）
        - 应该返回全库推荐
        """
        mock_retrieval = MagicMock()
        profile1 = create_mock_profile('wrk_expert1')
        profile2 = create_mock_profile('wrk_expert2')

        mock_retrieval.retrieve.return_value = RetrievalResponse(
            results=[
                RetrievalResult(profile=profile1, total_score=0.9),
                RetrievalResult(profile=profile2, total_score=0.8),
            ],
            question='',
            mode=RetrievalMode.EXPERT_DIAGNOSIS
        )

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval,
            min_experts=3,
        )

        result = service.recommend(
            question='test question',
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,  # 没有指定
            strict_participants=True,
        )

        # 应该返回全库推荐
        assert len(result.recommendations) == 2
        assert all(r.is_supplement == True for r in result.recommendations)


class TestCandidateRecommendationStrictModeIntegration:
    """候选推荐服务 strict 模式集成测试"""

    def test_strict_mode_does_not_affect_explicit_participants_loading(self):
        """
        验证：strict 模式不影响显式 participants 的加载

        显式 participants 应该始终被尝试加载
        """
        mock_retrieval = MagicMock()
        existing_profile = create_mock_profile('wrk_test')

        mock_retrieval.retrieve.return_value = RetrievalResponse(
            results=[RetrievalResult(profile=existing_profile, total_score=0.9)],
            question='',
            mode=RetrievalMode.EXPERT_DIAGNOSIS
        )

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval,
            min_experts=3,
        )

        # strict=True
        result = service.recommend(
            question='test question',
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=['wrk_test:default'],
            strict_participants=True,
        )

        # 应该找到显式 participant
        assert len(result.recommendations) == 1
        assert result.recommendations[0].profile_key == 'staff_wrk_test:default'
        assert result.recommendations[0].is_supplement == False

    def test_strict_mode_parameter_propagated_correctly(self):
        """
        验证：strict_participants 参数正确传递
        """
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve.return_value = RetrievalResponse(
            results=[],
            question='',
            mode=RetrievalMode.EXPERT_DIAGNOSIS
        )

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval,
            min_experts=3,
        )

        # 调用带 strict=True
        service.recommend(
            question='test',
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=['test:default'],
            strict_participants=True,
        )

        # 验证只调用了一次（没有补充推荐）
        assert mock_retrieval.retrieve.call_count == 1