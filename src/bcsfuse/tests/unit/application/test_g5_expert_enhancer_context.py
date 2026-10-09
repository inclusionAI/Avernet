"""G5 context selection, fallback, and sparse-context preflight coverage."""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from src.domain.models.fusion_result import Perspective
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.worker_profile import WorkerProfile
from src.domain.models.retrieval_mode import RetrievalMode
from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl


from tests.unit.application.test_g5_expert_enhancer_impl import (
    mock_dependencies as mock_dependencies,
    sample_profile as sample_profile,
    sample_digest as sample_digest,
)


class TestG5ExpertEnhancerImplRicherContextPack:
    """G5ExpertEnhancerImpl 更丰富 context pack 测试 (Phase 3)"""

    @pytest.fixture
    def enhancer(self):
        """创建 enhancer 实例"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        return G5ExpertEnhancerImpl(
            gateway=Mock(),
            retrieval_service=Mock(),
            preparation_service=Mock(),
            profile_source=Mock(),
        )

    def test_build_context_pack_selects_most_relevant_highlights(self, enhancer):
        """测试 context pack 选择最相关的 highlights"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragments = [
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in security architecture with 10 years experience.",
                source_path="/test/AGENTS.md",
            ),
            ContextFragment(
                kind=ContextKind.SOUL,
                filename="SOUL.md",
                content="General guidance.",
                source_path="/test/SOUL.md",
            ),
        ]

        from src.domain.models.skill_profile import SkillProfile
        skills = [
            SkillProfile(
                name="Security",
                description="Security testing",
                skill_id="skill_sec_001",
                skill_set_name="security",
            ),
        ]

        question = "How to perform security testing?"

        highlights = enhancer._select_context_highlights(
            fragments=fragments,
            question=question,
            max_highlights=2,
        )

        assert len(highlights) <= 2
        # 高亮内容应该存在
        assert all(isinstance(h, str) for h in highlights)

    def test_build_context_pack_selects_most_relevant_skills(self, enhancer):
        """测试 context pack 选择最相关的 skills"""
        from src.domain.models.skill_profile import SkillProfile

        skills = [
            SkillProfile(
                name="Security",
                description="Security testing",
                skill_id="skill_sec_001",
                skill_set_name="security",
            ),
            SkillProfile(
                name="Python",
                description="Python programming",
                skill_id="skill_py_001",
                skill_set_name="programming",
            ),
        ]

        question = "How to perform security testing?"

        selected_skills = enhancer._select_relevant_skills(
            skills=skills,
            question=question,
            max_skills=2,
        )

        assert len(selected_skills) <= 2
        # Security 应该被选中（因为与问题相关）
        skill_names = [s.name for s in selected_skills]
        assert "Security" in skill_names

    def test_build_context_pack_preserves_profile_key(self, enhancer):
        """测试 context pack 保留 profile_key"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Test question",
            relevant_fragments=[],
            relevant_skills=[],
            context_summary="Expert in testing",
        )

        pack = enhancer._build_expert_context_pack(
            question="Test",
            digest=digest,
            domain="tech",
        )

        # profile_key 应该包含原始 profile 信息
        assert "staff_001:default" in pack.profile_key

    def test_enhance_uses_richer_context_in_prompt_building(self):
        """测试 enhance 使用更丰富的 context 构建 prompt"""
        from unittest.mock import Mock, patch, MagicMock
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile
        from src.domain.models.worker_profile import ProfileType, WorkerProfile
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        # 创建有丰富内容的 profile
        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.AGENT,
                    filename="AGENTS.md",
                    content="Expert in security architecture with extensive penetration testing experience.",
                    source_path="/test/AGENTS.md",
                ),
            ],
            active_skills=[
                SkillProfile(
                    name="Security Testing",
                    description="Web application security testing",
                    skill_id="skill_sec_001",
                    skill_set_name="security",
                ),
            ],
        )

        gateway = Mock()
        retrieval = Mock()
        preparation = Mock()
        source = Mock()

        # 配置 mock
        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=profile, total_score=0.9)
        ])
        preparation.prepare.return_value = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="How to perform security testing?",
            relevant_fragments=profile.context_fragments,
            relevant_skills=profile.active_skills,
            context_summary="Security expert",
            # 添加 sparse context 检测所需的属性
            total_fragments=1,
            total_skills=1,
            selected_fragments=1,
            selected_skills=1,
        )
        gateway.generate.return_value = Mock(
            parse_success=True,
            structured_data={
                "summary": "Expert perspective",
                "confidence": 0.85,
                "key_points": [],
                "concerns": [],
                "risk_level": "low",
                "rationale_summary": "Based on expertise",
                "evidence_summary": [],
            }
        )

        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        # 执行 enhance
        result = enhancer.enhance(
            question="How to perform security testing?",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 验证 LLM 被调用，且 context 被传递
        assert gateway.generate.called
        call_args = gateway.generate.call_args
        request = call_args[0][0]

        # user_prompt 应该包含更丰富的 context
        assert "security" in request.user_prompt.lower() or "Security" in request.user_prompt


class TestG5ExpertEnhancerImplFallbackStillWorks:
    """G5ExpertEnhancerImpl fallback 仍有效测试 (Phase 3 回归)"""

    def test_parse_failure_still_falls_back(self, mock_dependencies, sample_profile, sample_digest):
        """测试 parse failure fallback 仍然有效"""
        gateway, retrieval, preparation, source = mock_dependencies

        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
        gateway.generate.return_value = Mock(
            parse_success=False,
            structured_data=None,
        )

        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        result = enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 应该返回 fallback 视角
        assert len(result) > 0
        assert result[0].confidence < 0.8  # fallback 低置信度
        assert "parse_failure" in result[0].concerns[0]

    def test_llm_failure_still_falls_back(self, mock_dependencies, sample_profile, sample_digest):
        """测试 LLM failure fallback 仍然有效"""
        gateway, retrieval, preparation, source = mock_dependencies

        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
        gateway.generate.side_effect = Exception("LLM error")

        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        result = enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 应该返回 fallback 视角
        assert len(result) > 0
        assert result[0].confidence < 0.8  # fallback 低置信度


# =============================================================================
# Stage 4 Phase 4: G5 Sparse Context Preflight Tests
# =============================================================================

class TestG5SparseContextPreflight:
    """G5 Sparse Context Preflight 测试"""

    @pytest.fixture
    def enhancer(self):
        """创建 enhancer 实例"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        return G5ExpertEnhancerImpl(
            gateway=Mock(),
            retrieval_service=Mock(),
            preparation_service=Mock(),
            profile_source=Mock(),
        )

    @pytest.fixture
    def empty_profile(self):
        """创建空的 profile（无 fragments, 无 skills）"""
        from src.domain.models.worker_profile import ProfileType
        return WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[],
            active_skills=[],
        )

    @pytest.fixture
    def sparse_profile_with_placeholder(self):
        """创建带 placeholder 的 profile"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.worker_profile import ProfileType
        return WorkerProfile(
            staff_id="002",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.AGENT,
                    filename="AGENTS.md",
                    content="Expert profile - no relevant context available.",
                    source_path="/test/AGENTS.md",
                ),
            ],
            active_skills=[],
        )

    @pytest.fixture
    def rich_profile(self):
        """创建有丰富内容的 profile"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile
        from src.domain.models.worker_profile import ProfileType
        return WorkerProfile(
            staff_id="003",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.AGENT,
                    filename="AGENTS.md",
                    content="Expert in Python with 5 years of experience in API design.",
                    source_path="/test/AGENTS.md",
                ),
            ],
            active_skills=[
                SkillProfile(
                    name="Python",
                    description="Python programming",
                    skill_id="skill_py_001",
                    skill_set_name="programming",
                ),
            ],
        )

    @pytest.fixture
    def empty_digest(self):
        """创建空的 digest"""
        return WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Test question",
            relevant_fragments=[],
            relevant_skills=[],
            context_summary="",
        )

    @pytest.fixture
    def rich_digest(self):
        """创建有内容的 digest"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.AGENT,
            filename="AGENTS.md",
            content="Expert in Python with 5 years of experience in API design.",
            source_path="/test/AGENTS.md",
        )

        return WorkerContextDigest(
            profile_key="staff_003:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Test question",
            relevant_fragments=[fragment],
            relevant_skills=[],
            context_summary="Expert in Python with API design experience",
            # 添加 sparse context 检测所需的属性
            total_fragments=1,
            total_skills=1,
            selected_fragments=1,
            selected_skills=1,
        )

    def test_sparse_context_empty_profile_should_skip(self, enhancer, empty_profile, empty_digest):
        """测试空 profile 应触发 sparse context 跳过"""
        from src.application.services.g5_expert_enhancer_impl import _should_skip_llm_for_sparse_context

        should_skip, reason = _should_skip_llm_for_sparse_context(empty_profile, empty_digest)

        assert should_skip is True
        assert "fragments" in reason.lower() or "skills" in reason.lower()

    def test_sparse_context_placeholder_summary_should_skip(self, enhancer, sparse_profile_with_placeholder):
        """测试 placeholder summary 应触发 sparse context 跳过"""
        from src.application.services.g5_expert_enhancer_impl import _should_skip_llm_for_sparse_context

        digest = WorkerContextDigest(
            profile_key="staff_002:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Test",
            relevant_fragments=[],
            relevant_skills=[],
            context_summary="Expert profile - no relevant context",
        )

        should_skip, reason = _should_skip_llm_for_sparse_context(sparse_profile_with_placeholder, digest)

        assert should_skip is True
        assert "placeholder" in reason.lower() or "context" in reason.lower()

    def test_rich_context_should_not_skip(self, enhancer, rich_profile, rich_digest):
        """测试丰富 context 不应触发跳过"""
        from src.application.services.g5_expert_enhancer_impl import _should_skip_llm_for_sparse_context

        should_skip, reason = _should_skip_llm_for_sparse_context(rich_profile, rich_digest)

        assert should_skip is False

    def test_sparse_context_perspective_structure(self, enhancer, empty_profile):
        """测试 sparse context perspective 结构"""
        from src.application.services.g5_expert_enhancer_impl import _build_sparse_context_perspective

        perspective = _build_sparse_context_perspective(
            profile=empty_profile,
            question="Test question",
            skip_reason="No context available",
        )

        assert perspective.status == "skipped"
        assert perspective.confidence <= 0.2
        # summary 是中文，检查上下文不足关键信息
        assert "不足" in perspective.summary or "sparse" in perspective.summary.lower() or "context" in perspective.summary.lower()
        assert perspective.role == "expert"
        assert perspective.participant_id == "001:default"

    def test_enhance_with_sparse_context_does_not_call_llm(self, mock_dependencies, empty_profile, empty_digest):
        """测试 sparse context 不会调用 LLM"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock
        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=empty_profile, total_score=0.5)
        ])
        preparation.prepare.return_value = empty_digest

        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        result = enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 不应该调用 LLM gateway
        gateway.generate.assert_not_called()
        # 应该返回 skipped perspective
        assert len(result) > 0
        assert result[0].status == "skipped"


class TestG5StrictParticipantsWithSparseContext:
    """G5 strict_participants 与 sparse context 交互测试"""

    def test_strict_true_with_empty_profiles_returns_empty(self, mock_dependencies):
        """测试 strict=true + 空 profiles 返回空结果（不 fallback）"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock - retrieval 返回空
        retrieval.retrieve.return_value = Mock(results=[])

        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        base = [Perspective(
            participant_id="staff_001",
            participant_type="bot",
            role="consultant",
            summary="Base",
            status="completed",
        )]

        result = enhancer.enhance(
            question="Test",
            base_perspectives=base,
            participants=["staff_001"],
            strict_participants=True,
        )

        # strict=true 且没有找到 profile，应返回空列表（不 fallback 到 base）
        # 这是正确的 strict 语义：明确要求 participants 但找不到时返回空
        assert result == []
        # 不应该调用 LLM
        gateway.generate.assert_not_called()


class TestG5PreflightLogging:
    """G5 Preflight 日志测试"""

    def test_sparse_context_logs_preflight_skip(self, mock_dependencies, caplog):
        """测试 sparse context 记录 preflight skip 日志"""
        import logging

        caplog.set_level(logging.INFO)

        from src.domain.models.worker_profile import ProfileType

        empty_profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[],
            active_skills=[],
        )

        gateway, retrieval, preparation, source = mock_dependencies

        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=empty_profile, total_score=0.5)
        ])
        preparation.prepare.return_value = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Test",
            relevant_fragments=[],
            relevant_skills=[],
            context_summary="",
        )

        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        enhancer.enhance(
            question="Test",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 检查日志包含 G5-ENHANCER-PREFLIGHT 标识
        log_text = caplog.text
        assert "G5-ENHANCER-PREFLIGHT" in log_text or "sparse" in log_text.lower()
