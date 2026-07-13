"""
WorkerCandidateRecommendationImpl Unit Tests

Stage 4 Phase 2: Candidate Recommendation Service

测试覆盖：
1. 无 participants 场景
2. 显式 participants 充足场景
3. 显式 participants 不足场景
4. 输出顺序保证
5. 领域覆盖分析
6. retrieval 失败降级
7. max_candidates 控制
8. EXPERT_DIAGNOSIS 模式
"""

from __future__ import annotations

from unittest.mock import Mock
import pytest

from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.skill_profile import SkillProfile
from src.domain.services.participants_sufficiency_checker import SufficiencyCheckResult
from src.application.services.worker_candidate_recommendation_impl import (
    WorkerCandidateRecommendationImpl,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_profile_1():
    """创建测试用 WorkerProfile 1"""
    return WorkerProfile(
        staff_id="001",  # 修改：不带 staff_ 前缀，profile_key 会变成 staff_001:default
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_root="/test/profiles",
        context_fragments=[
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in security architecture and authentication systems.",
                source_path="/test/profiles/staff_001/default/openclaw/AGENTS.md",
            ),
        ],
        active_skills=[
            SkillProfile(
                name="Security",
                description="Security architecture design",
                skill_id="skill_sec_001",
                skill_set_name="security",
            ),
        ],
    )


@pytest.fixture
def sample_profile_2():
    """创建测试用 WorkerProfile 2"""
    return WorkerProfile(
        staff_id="002",  # 修改：不带 staff_ 前缀
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_root="/test/profiles",
        context_fragments=[
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in legal compliance and data privacy regulations.",
                source_path="/test/profiles/staff_002/default/openclaw/AGENTS.md",
            ),
        ],
        active_skills=[
            SkillProfile(
                name="Legal",
                description="Legal compliance",
                skill_id="skill_legal_001",
                skill_set_name="legal",
            ),
        ],
    )


@pytest.fixture
def sample_profile_3():
    """创建测试用 WorkerProfile 3"""
    return WorkerProfile(
        staff_id="003",  # 修改：不带 staff_ 前缀
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_root="/test/profiles",
        context_fragments=[
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in database design and optimization.",
                source_path="/test/profiles/staff_003/default/openclaw/AGENTS.md",
            ),
        ],
        active_skills=[
            SkillProfile(
                name="Database",
                description="Database architecture",
                skill_id="skill_db_001",
                skill_set_name="database",
            ),
        ],
    )


@pytest.fixture
def mock_retrieval_service(sample_profile_1, sample_profile_2, sample_profile_3):
    """创建 mock retrieval service"""
    service = Mock()

    # 默认返回检索结果
    def mock_retrieve(question, mode, top_k=None, profile_keys=None, **kwargs):
        profiles = [sample_profile_1, sample_profile_2, sample_profile_3]
        if profile_keys:
            profiles = [p for p in profiles if p.profile_key in profile_keys]

        from src.domain.services.worker_profile_retrieval_service import (
            RetrievalResult,
            RetrievalResponse,
        )
        results = [
            RetrievalResult(profile=p, total_score=0.8 + i * 0.05)
            for i, p in enumerate(profiles[:top_k] if top_k else profiles)
        ]
        return RetrievalResponse(
            results=results,
            question=question,
            mode=mode,
        )

    service.retrieve.side_effect = mock_retrieve
    return service


@pytest.fixture
def recommendation_impl(mock_retrieval_service):
    """创建 WorkerCandidateRecommendationImpl 实例"""
    return WorkerCandidateRecommendationImpl(
        retrieval_service=mock_retrieval_service,
    )


# =============================================================================
# Test Classes
# =============================================================================

class TestWorkerCandidateRecommendationImplInit:
    """初始化测试"""

    def test_init_with_retrieval_service(self, mock_retrieval_service):
        """测试初始化需要 retrieval_service"""
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
        )
        assert impl._retrieval_service == mock_retrieval_service

    def test_init_with_default_min_experts(self, mock_retrieval_service):
        """测试默认 min_experts=3"""
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
        )
        assert impl._min_experts == 3

    def test_init_with_custom_min_experts(self, mock_retrieval_service):
        """测试自定义 min_experts"""
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            min_experts=5,
        )
        assert impl._min_experts == 5


class TestWorkerCandidateRecommendationImplRecommend:
    """核心 recommend 方法测试"""

    # =========================================================================
    # 场景 A：无 participants
    # =========================================================================

    def test_recommend_without_participants_returns_candidates(
        self,
        recommendation_impl,
    ):
        """
        场景 A：无 participants
        - 直接推荐候选人
        - participants_given = false
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        assert isinstance(response, CandidateRecommendationResponse)
        assert response.participants_given is False
        assert len(response.recommendations) > 0

    def test_recommend_without_participants_all_marked_supplement(
        self,
        recommendation_impl,
    ):
        """
        场景 A：无 participants 时，所有推荐都标记为 supplement
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 所有推荐应该是 supplement
        assert all(r.is_supplement for r in response.recommendations)

    # =========================================================================
    # 场景 B：显式 participants 充足
    # =========================================================================

    def test_recommend_with_sufficient_explicit_participants_does_not_supplement(
        self,
        recommendation_impl,
    ):
        """
        场景 B：显式 participants 充足
        - 显式 participants 进入结果集
        - 不额外补充
        - participants_sufficient = true
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],  # profile_key 格式
        )

        assert response.participants_given is True
        assert response.participants_sufficient is True
        # 只应该有显式 participants，无补充
        assert len(response.supplement_candidates) == 0

    def test_explicit_candidates_marked_is_supplement_false(
        self,
        recommendation_impl,
    ):
        """
        显式 participants 标记为 is_supplement=False
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],  # profile_key 格式
        )

        # 所有推荐应该是显式（非 supplement）
        assert all(not r.is_supplement for r in response.recommendations)

    # =========================================================================
    # 场景 C：显式 participants 不足
    # =========================================================================

    def test_recommend_with_insufficient_participants_adds_supplements(
        self,
        recommendation_impl,
    ):
        """
        场景 C：显式 participants 不足
        - 显式 participants 保留
        - 补充推荐
        - 补充项 is_supplement = true
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default"],  # 只有 1 个，不足 min_experts=3
        )

        assert response.participants_given is True
        assert response.participants_sufficient is False
        # 应该有补充
        assert len(response.supplement_candidates) > 0

    def test_explicit_participants_preserved_when_insufficient(
        self,
        recommendation_impl,
    ):
        """
        显式 participants 不足时，仍然保留在结果中
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default"],
        )

        # 显式 participant 应该在结果中
        profile_keys = [r.profile_key for r in response.explicit_participants]
        assert "staff_001:default" in profile_keys

    # =========================================================================
    # 输出顺序保证
    # =========================================================================

    def test_explicit_participants_appear_before_supplements(
        self,
        recommendation_impl,
    ):
        """
        输出顺序保证：显式在前，补充在后
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default"],  # 使用正确的 profile_key 格式
        )

        # 找到第一个 supplement 的位置
        first_supplement_idx = None
        for i, r in enumerate(response.recommendations):
            if r.is_supplement:
                first_supplement_idx = i
                break

        # 找到最后一个显式的位置
        last_explicit_idx = None
        for i, r in enumerate(response.recommendations):
            if not r.is_supplement:
                last_explicit_idx = i

        # 如果两者都存在，显式应该在补充前面
        if first_supplement_idx is not None and last_explicit_idx is not None:
            assert last_explicit_idx < first_supplement_idx

    # =========================================================================
    # 领域覆盖分析
    # =========================================================================

    def test_recommend_returns_domain_coverage(
        self,
        recommendation_impl,
    ):
        """
        返回 domain_coverage 分析
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        assert response.domain_coverage is not None
        assert isinstance(response.domain_coverage, DomainCoverage)

    # =========================================================================
    # 场景 D：retrieval 失败降级
    # =========================================================================

    def test_retrieval_failure_returns_degraded_response(
        self,
        mock_retrieval_service,
    ):
        """
        场景 D：retrieval 失败时返回降级响应
        """
        # 让 retrieval 抛出异常
        mock_retrieval_service.retrieve.side_effect = Exception("Retrieval failed")

        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
        )

        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 应该返回空响应而不是抛异常
        assert isinstance(response, CandidateRecommendationResponse)
        assert response.participants_given is False
        assert len(response.recommendations) == 0

    def test_retrieval_empty_results_returns_empty_response(
        self,
        sample_profile_1,
        sample_profile_2,
        sample_profile_3,
    ):
        """
        retrieval 返回空结果时返回空响应
        """
        from src.domain.services.worker_profile_retrieval_service import (
            RetrievalResponse,
        )
        from unittest.mock import Mock

        # 创建新的 mock，避免与 fixture 共享状态
        fresh_mock = Mock()
        fresh_mock.retrieve.return_value = RetrievalResponse(
            results=[],
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
        )

        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=fresh_mock,
        )

        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        assert len(response.recommendations) == 0

    # =========================================================================
    # max_candidates 控制
    # =========================================================================

    def test_recommend_respects_max_candidates(
        self,
        recommendation_impl,
    ):
        """
        max_candidates 参数控制返回数量
        """
        response = recommendation_impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
            max_candidates=2,
        )

        assert len(response.recommendations) <= 2

    # =========================================================================
    # EXPERT_DIAGNOSIS 模式
    # =========================================================================

    def test_recommend_uses_expert_diagnosis_mode(
        self,
        mock_retrieval_service,
    ):
        """
        确保 retrieval 使用 EXPERT_DIAGNOSIS 模式
        """
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
        )

        impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 验证 retrieval 被调用时使用了正确的模式
        mock_retrieval_service.retrieve.assert_called()
        call_kwargs = mock_retrieval_service.retrieve.call_args
        assert call_kwargs[1]["mode"] == RetrievalMode.EXPERT_DIAGNOSIS

    # =========================================================================
    # min_experts 参数
    # =========================================================================

    def test_recommend_uses_min_experts_for_sufficiency_check(
        self,
        sample_profile_1,
        sample_profile_2,
    ):
        """
        min_experts 参数用于充足性检查
        """
        from unittest.mock import Mock
        from src.domain.services.worker_profile_retrieval_service import (
            RetrievalResult,
            RetrievalResponse,
        )

        # 创建新的 mock
        fresh_mock = Mock()

        def mock_retrieve(question, mode, top_k=None, profile_keys=None, **kwargs):
            profiles = [sample_profile_1, sample_profile_2]
            if profile_keys:
                profiles = [p for p in profiles if p.profile_key in profile_keys]

            results = [
                RetrievalResult(profile=p, total_score=0.8 + i * 0.05)
                for i, p in enumerate(profiles[:top_k] if top_k else profiles)
            ]
            return RetrievalResponse(
                results=results,
                question=question,
                mode=mode,
            )

        fresh_mock.retrieve.side_effect = mock_retrieve

        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=fresh_mock,
            min_experts=2,  # 设置为 2
        )

        # 给 2 个 participants (profile_key 格式：staff_xxx:default)
        response = impl.recommend(
            question="Test",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default", "staff_002:default"],
        )

        # 2 个 >= min_experts=2，应该充足
        assert response.participants_sufficient is True


class TestWorkerCandidateRecommendationImplHelpers:
    """内部辅助方法测试"""

    def test_infer_domain_from_profile(self, recommendation_impl, sample_profile_1):
        """测试从 profile 推断领域"""
        domain = recommendation_impl._infer_domain(sample_profile_1)
        assert domain is not None
        assert isinstance(domain, str)

    def test_build_recommendation_from_profile(
        self,
        recommendation_impl,
        sample_profile_1,
    ):
        """测试从 profile 构建推荐"""
        rec = recommendation_impl._build_recommendation_from_profile(
            profile=sample_profile_1,
            score=0.85,
            is_supplement=True,
        )

        assert rec.profile_key == sample_profile_1.profile_key
        assert rec.score == 0.85
        assert rec.is_supplement is True
        assert isinstance(rec.reasons, list)


# =============================================================================
# Vector-Aware Integration Tests (Phase 3)
# =============================================================================

class TestVectorMatchIntegration:
    """Vector Match 集成测试

    测试 WorkerVectorMatchService 与 WorkerCandidateRecommendationImpl 的集成。

    核心约定：
    1. G5-first: 只有 EXPERT_DIAGNOSIS 模式使用 vector match
    2. 显式 participants 永远优先
    3. Vector match 失败时 graceful fallback 到现有 keyword 检索
    4. 不影响 G1/G2 模式行为
    """

    # =========================================================================
    # Fixture: Mock Vector Match Service
    # =========================================================================

    @pytest.fixture
    def mock_vector_match_service(self, sample_profile_1, sample_profile_2, sample_profile_3):
        """创建 mock vector match service"""
        from src.application.services.worker_vector_match_service import (
            MatchResult,
        )
        from src.domain.models.metadata_record import MetadataRecord
        from unittest.mock import Mock

        service = Mock()

        # 创建 MetadataRecord 的辅助函数
        def make_metadata(profile):
            return MetadataRecord(
                profile_key=profile.profile_key,
                domains=[profile.active_skills[0].name.lower()] if profile.active_skills else [],
                active_skill_names=[s.name for s in profile.active_skills],
                metadata={"staff_id": profile.staff_id},
            )

        def mock_match(query_embedding, top_k, filters=None, excluded_profile_keys=None, **kwargs):
            profiles = [sample_profile_1, sample_profile_2, sample_profile_3]
            excluded_set = set(excluded_profile_keys or [])

            results = []
            for i, p in enumerate(profiles):
                if p.profile_key in excluded_set:
                    continue
                results.append(MatchResult(
                    profile_key=p.profile_key,
                    metadata=make_metadata(p),
                    score=0.9 - i * 0.1,
                    reasons=["Vector similarity: 0.90"],
                ))
                if len(results) >= top_k:
                    break
            return results

        service.match.side_effect = mock_match
        return service

    @pytest.fixture
    def mock_embedding_generator(self):
        """创建 mock embedding generator"""
        from unittest.mock import Mock

        generator = Mock()
        generator.generate.return_value = [0.1] * 384  # Fake embedding
        return generator

    @pytest.fixture
    def recommendation_impl_with_vector(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
    ):
        """创建带 vector match 能力的 WorkerCandidateRecommendationImpl"""
        return WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

    # =========================================================================
    # Test 1: G5 Uses Vector Match Service When Available
    # =========================================================================

    def test_g5_uses_vector_match_service_when_available(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
    ):
        """
        G5 模式下，如果 vector_match_service 可用，应该使用它来获取补充推荐
        """
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

        # 无显式 participants，需要补充
        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 应该调用 vector match service
        mock_vector_match_service.match.assert_called_once()

        # 验证返回了推荐
        assert len(response.recommendations) > 0

    # =========================================================================
    # Test 2: Non-G5 Does NOT Use Vector Match Service
    # =========================================================================

    def test_non_g5_does_not_use_vector_match_service(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
    ):
        """
        非 G5 模式（如 G1/G2）不应该使用 vector match service
        """
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

        # 使用 AGENT 模式 (G1，非 G5)
        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.AGENT,  # G1，非 G5
            participants=None,
        )

        # 不应该调用 vector match service
        mock_vector_match_service.match.assert_not_called()

        # 应该使用 retrieval service (fallback)
        assert mock_retrieval_service.retrieve.called

    # =========================================================================
    # Test 3: Vector Match Failure Falls Back to Existing Logic
    # =========================================================================

    def test_vector_match_failure_falls_back_to_existing_logic(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
    ):
        """
        Vector match service 失败时，应该 fallback 到现有的 retrieval service
        """
        # 让 vector match 抛出异常
        mock_vector_match_service.match.side_effect = Exception("Vector store error")

        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 应该返回结果（通过 fallback）
        assert isinstance(response, CandidateRecommendationResponse)
        # retrieval service 应该被调用 (fallback)
        assert mock_retrieval_service.retrieve.called

    # =========================================================================
    # Test 4: Vector Match Empty Result Falls Back
    # =========================================================================

    def test_vector_match_empty_result_falls_back(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
    ):
        """
        Vector match 返回空结果时，应该 fallback 到现有的 retrieval service
        """
        # 让 vector match 返回空列表
        mock_vector_match_service.match.return_value = []

        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 应该返回结果（通过 fallback）
        assert isinstance(response, CandidateRecommendationResponse)
        # retrieval service 应该被调用 (fallback)
        assert mock_retrieval_service.retrieve.called

    # =========================================================================
    # Test 5: Explicit Participants Still Priority With Vector Enabled
    # =========================================================================

    def test_explicit_participants_still_priority_with_vector_enabled(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
        sample_profile_1,
    ):
        """
        即使 vector match 可用，显式 participants 仍然优先返回
        """
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
            min_experts=3,  # 需要 3 个
        )

        # 提供 1 个显式 participant (不足 min_experts)
        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default"],
        )

        # 显式 participant 应该在结果中，且 is_supplement=False
        explicit_keys = [r.profile_key for r in response.explicit_participants]
        assert "staff_001:default" in explicit_keys

        # 所有显式 participant 都是 is_supplement=False
        for r in response.explicit_participants:
            assert r.is_supplement is False

        # 补充项应该是 is_supplement=True
        for r in response.supplement_candidates:
            assert r.is_supplement is True

    # =========================================================================
    # Test 6: Output Contract Unchanged With Vector Enabled
    # =========================================================================

    def test_output_contract_unchanged_with_vector_enabled(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
    ):
        """
        启用 vector match 后，输出契约保持不变
        - 返回 CandidateRecommendationResponse
        - 包含正确的字段
        """
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
        )

        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 验证输出契约
        assert isinstance(response, CandidateRecommendationResponse)
        assert response.question == "How to secure the API?"
        assert response.mode == RetrievalMode.EXPERT_DIAGNOSIS
        assert isinstance(response.domain_coverage, DomainCoverage)
        assert isinstance(response.recommendations, list)
        assert isinstance(response.total_candidates, int)
        assert isinstance(response.selected_candidates, int)

    # =========================================================================
    # Test 7: Without Vector Match Service Falls Back Gracefully
    # =========================================================================

    def test_without_vector_match_service_uses_retrieval_only(
        self,
        mock_retrieval_service,
    ):
        """
        没有提供 vector_match_service 时，G5 模式仍然可用 retrieval service
        """
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            # 不提供 vector_match_service
        )

        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=None,
        )

        # 应该返回结果（通过 retrieval service）
        assert isinstance(response, CandidateRecommendationResponse)
        assert len(response.recommendations) > 0
        assert mock_retrieval_service.retrieve.called

    # =========================================================================
    # Test 8: Excluded Profile Keys Passed to Vector Match
    # =========================================================================

    def test_excluded_profile_keys_passed_to_vector_match(
        self,
        mock_retrieval_service,
        mock_vector_match_service,
        mock_embedding_generator,
    ):
        """
        显式 participants 应该被排除在 vector match 结果之外
        """
        impl = WorkerCandidateRecommendationImpl(
            retrieval_service=mock_retrieval_service,
            vector_match_service=mock_vector_match_service,
            embedding_generator=mock_embedding_generator,
            min_experts=3,
        )

        response = impl.recommend(
            question="How to secure the API?",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            participants=["staff_001:default"],  # 1 个显式，不足 min_experts
        )

        # 检查 vector match 被调用时传入了正确的 excluded_profile_keys
        call_args = mock_vector_match_service.match.call_args
        excluded = call_args[1].get("excluded_profile_keys", [])
        assert "staff_001:default" in excluded