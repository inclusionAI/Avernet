from __future__ import annotations
import pytest
from unittest.mock import Mock, MagicMock
from fastapi.testclient import TestClient
from src.domain.models.fusion_result import Perspective
from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.skill_profile import SkillProfile
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
from src.interfaces.api.fusion_routes import router, set_provider
from fastapi import FastAPI
from tests.integration.test_g5_candidate_recommendation_flow import client, mock_retrieval_service, sample_recommendation_response
from tests.integration.test_g5_candidate_recommendation_flow import MockG5ProviderWithRecommendation

class TestG5VectorMatchIntegration:
    """
    G5 向量匹配集成测试

    测试 WorkerVectorMatchService 与 G5 候选人推荐的端到端集成。

    核心验证：
    1. G5 模式下向量匹配生效
    2. 非 G5 模式下向量匹配不生效
    3. 向量匹配失败时返回空补充列表
    4. 输出契约保持不变
    """

    @pytest.fixture
    def mock_vector_match_service(self):
        """创建 mock vector match service"""
        from src.application.services.worker_vector_match_service import MatchResult
        from src.domain.models.metadata_record import MetadataRecord

        service = Mock()

        def mock_match(query_embedding, top_k, filters=None, excluded_profile_keys=None, **kwargs):
            # 返回模拟的匹配结果
            results = [
                MatchResult(
                    profile_key="vector_expert_001:default",
                    metadata=MetadataRecord(
                        profile_key="vector_expert_001:default",
                        domains=["security"],
                        active_skill_names=["Security"],
                        staff_id="vector_expert_001", profile_id="default",
                        profile_type="default", source_root="/test",
                    ),
                    score=0.95,
                    reasons=["Vector similarity: 0.95"],
                ),
                MatchResult(
                    profile_key="vector_expert_002:default",
                    metadata=MetadataRecord(
                        profile_key="vector_expert_002:default",
                        domains=["database"],
                        active_skill_names=["Database"],
                        staff_id="vector_expert_002", profile_id="default",
                        profile_type="default", source_root="/test",
                    ),
                    score=0.90,
                    reasons=["Vector similarity: 0.90"],
                ),
            ]
            return [result for result in results if result.profile_key not in (excluded_profile_keys or [])][:top_k]

        service.match.side_effect = mock_match
        return service

    @pytest.fixture
    def mock_embedding_generator(self):
        """创建 mock embedding generator"""
        generator = Mock()
        generator.embed.return_value = [0.1] * 384
        return generator

    @pytest.fixture
    def vector_aware_retrieval_service(self):
        """创建支持向量匹配的 retrieval service"""
        from src.domain.services.worker_profile_retrieval_service import (
            RetrievalResult,
            RetrievalResponse,
        )

        service = Mock()

        # 创建向量匹配专用的 profiles
        vector_profile_1 = WorkerProfile(
            staff_id="vector_expert_001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test/profiles",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.AGENT,
                    filename="AGENTS.md",
                    content="Expert in security architecture.",
                    source_path="/test/profiles/vector_expert_001/default/openclaw/AGENTS.md",
                ),
            ],
            active_skills=[
                SkillProfile(
                    name="Security",
                    description="Security architecture",
                    skill_id="skill_vec_sec",
                    skill_set_name="security",
                ),
            ],
        )

        vector_profile_2 = WorkerProfile(
            staff_id="vector_expert_002",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test/profiles",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.AGENT,
                    filename="AGENTS.md",
                    content="Expert in database systems.",
                    source_path="/test/profiles/vector_expert_002/default/openclaw/AGENTS.md",
                ),
            ],
            active_skills=[
                SkillProfile(
                    name="Database",
                    description="Database systems",
                    skill_id="skill_vec_db",
                    skill_set_name="database",
                ),
            ],
        )

        def mock_retrieve(question, mode, top_k=None, profile_keys=None, **kwargs):
            all_profiles = [vector_profile_1, vector_profile_2]

            if profile_keys:
                filtered = [p for p in all_profiles if p.profile_key in profile_keys]
            else:
                filtered = all_profiles

            results = [
                RetrievalResult(profile=p, total_score=0.8 + i * 0.05)
                for i, p in enumerate(filtered[:top_k] if top_k else filtered)
            ]
            return RetrievalResponse(
                results=results,
                question=question,
                mode=mode,
            )

        service.retrieve.side_effect = mock_retrieve
        return service, [vector_profile_1, vector_profile_2]

    def test_g5_uses_vector_match_for_supplement_recommendations(
        self,
        mock_vector_match_service,
        mock_embedding_generator,
        vector_aware_retrieval_service,
    ):
        """
        G5 模式下使用向量匹配获取补充推荐
        """
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )

        retrieval_svc, profiles = vector_aware_retrieval_service

        # 创建带向量匹配的服务
        service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_svc,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
            min_experts=3,
        )

        # 执行推荐（无 participants，需要补充）
        result = service.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 验证向量匹配被调用
        mock_vector_match_service.match.assert_called_once()

        # 验证返回了推荐
        assert len(result.recommendations) > 0

        # 验证所有推荐都是 supplement
        assert all(r.is_supplement for r in result.recommendations)

    def test_g1_mode_ignores_vector_match(
        self,
        mock_vector_match_service,
        mock_embedding_generator,
        vector_aware_retrieval_service,
    ):
        """
        G1 (AGENT) 模式不使用向量匹配
        """
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )

        retrieval_svc, profiles = vector_aware_retrieval_service

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_svc,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

        # 使用 AGENT 模式
        result = service.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.AGENT,
            participants=None,
        )

        # 向量匹配不应被调用
        mock_vector_match_service.match.assert_not_called()

        # 非 G5 不通过 retrieval 生成补充候选
        assert result.recommendations == []
        retrieval_svc.retrieve.assert_not_called()

    def test_vector_match_failure_returns_empty_without_keyword_fallback(
        self,
        mock_embedding_generator,
        vector_aware_retrieval_service,
    ):
        """
        向量匹配失败时不使用 keyword retrieval 扩大召回范围
        """
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )

        retrieval_svc, profiles = vector_aware_retrieval_service

        # 创建抛出异常的 mock vector match service
        failing_vector_service = Mock()
        failing_vector_service.match.side_effect = Exception("Vector store error")

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_svc,
            vector_match_service=failing_vector_service,
            embedding_generator=mock_embedding_generator,
        )

        # 执行推荐
        result = service.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 返回结构化的空候选响应
        assert isinstance(result, CandidateRecommendationResponse)
        assert result.recommendations == []
        retrieval_svc.retrieve.assert_not_called()

    def test_explicit_participants_priority_with_vector_match(
        self,
        mock_vector_match_service,
        mock_embedding_generator,
        vector_aware_retrieval_service,
    ):
        """
        显式 participants 优先级高于向量匹配结果
        """
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )

        retrieval_svc, profiles = vector_aware_retrieval_service

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_svc,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
            min_experts=3,
        )

        # 提供显式 participant（不足 min_experts）
        result = service.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["vector_expert_001:default"],
        )

        # 显式 participant 应该在结果中
        explicit_keys = [r.profile_key for r in result.explicit_participants]
        assert "vector_expert_001:default" in explicit_keys

        # 所有显式 participant 都是 is_supplement=False
        for r in result.explicit_participants:
            assert r.is_supplement is False

        # 补充项应该是 is_supplement=True
        for r in result.supplement_candidates:
            assert r.is_supplement is True

    def test_output_contract_stable_with_vector_match(
        self,
        mock_vector_match_service,
        mock_embedding_generator,
        vector_aware_retrieval_service,
    ):
        """
        启用向量匹配后输出契约保持稳定
        """
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )

        retrieval_svc, profiles = vector_aware_retrieval_service

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_svc,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

        result = service.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 验证输出契约
        assert isinstance(result, CandidateRecommendationResponse)
        assert result.question == "How to secure the API?"
        assert result.mode == RetrievalMode.EXPERT_DIAGNOSIS
        assert isinstance(result.domain_coverage, DomainCoverage)
        assert isinstance(result.recommendations, list)
        assert isinstance(result.total_candidates, int)
        assert isinstance(result.selected_candidates, int)

    def test_excluded_profile_keys_passed_correctly(
        self,
        mock_vector_match_service,
        mock_embedding_generator,
        vector_aware_retrieval_service,
    ):
        """
        显式 participants 被正确排除在向量匹配结果之外
        """
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )

        retrieval_svc, profiles = vector_aware_retrieval_service

        service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_svc,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
            min_experts=3,
        )

        # 提供显式 participant
        service.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["vector_expert_001:default"],
        )

        # 验证 excluded_profile_keys 被传入
        call_args = mock_vector_match_service.match.call_args
        excluded = call_args[1].get("excluded_profile_keys", [])
        assert "vector_expert_001:default" in excluded


class TestG5VectorMatchE2E:
    """
    G5 向量匹配端到端测试

    测试从 HTTP API 到向量匹配的完整链路。
    """

    @pytest.fixture
    def client_with_vector_match(self):
        """创建带向量匹配的测试客户端"""
        from src.application.services.worker_candidate_recommendation_impl import (
            WorkerCandidateRecommendationImpl,
        )
        from src.domain.services.worker_profile_retrieval_service import (
            RetrievalResult,
            RetrievalResponse,
        )
        from src.application.services.worker_vector_match_service import MatchResult
        from src.domain.models.metadata_record import MetadataRecord

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")

        # Mock services
        mock_retrieval = Mock()

        profile = WorkerProfile(
            staff_id="e2e_expert",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.AGENT,
                    filename="AGENTS.md",
                    content="Security expert",
                    source_path="/test/e2e_expert/default/openclaw/AGENTS.md",
                ),
            ],
            active_skills=[
                SkillProfile(
                    name="Security",
                    description="Security",
                    skill_id="skill_e2e",
                    skill_set_name="security",
                ),
            ],
        )

        def mock_retrieve(question, mode, top_k=None, profile_keys=None, **kwargs):
            if profile_keys:
                filtered = [p for p in [profile] if p.profile_key in profile_keys]
            else:
                filtered = [profile]
            results = [RetrievalResult(profile=p, total_score=0.9) for p in filtered]
            return RetrievalResponse(results=results, question=question, mode=mode)

        mock_retrieval.retrieve.side_effect = mock_retrieve

        mock_vector_match = Mock()
        mock_vector_match.match.return_value = [
            MatchResult(
                profile_key="staff_e2e_expert:default",
                metadata=MetadataRecord(
                    profile_key="staff_e2e_expert:default",
                    staff_id="e2e_expert",
                    profile_id="default",
                    profile_type="default",
                    source_root="/test",
                    domains=["security"],
                    active_skill_names=["Security"],
                ),
                score=0.95,
                reasons=["Vector similarity: 0.95"],
            ),
        ]

        mock_embedding = Mock()
        mock_embedding.embed.return_value = [0.1] * 384

        # 设置 provider
        provider = MockG5ProviderWithRecommendation({
            "staff_e2e_expert:default": Perspective(
                participant_id="staff_e2e_expert:default",
                participant_type="bot",
                role="expert",
                summary="E2E expert response",
                status="completed",
            ),
        })
        set_provider(provider)

        return TestClient(app)

    def test_g5_e2e_vector_match_enabled(
        self,
        client_with_vector_match: TestClient,
    ):
        """
        G5 端到端测试：向量匹配启用
        """
        response = client_with_vector_match.post(
            "/api/v1/groups/grp-e2e-test/fuse",
            json={
                "question": "Security review",
                "participants": ["staff_e2e_expert:default"],
                "fusion_mode": "expert_diagnosis",
                "options": {"strict_participants": False},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fusion_mode"] == "expert_diagnosis"
        assert len(data["perspectives"]) > 0


# Note: Real index tests (TestG5VectorMatchWithRealIndex) moved to
# tests/unit/infra/test_faiss_vector_store_adapter.py and
# tests/unit/application/test_worker_vector_match_service.py
# where infrastructure-level testing is more appropriate.
# The mock-based integration tests above provide sufficient coverage
# for the G5 candidate recommendation flow with vector matching.
