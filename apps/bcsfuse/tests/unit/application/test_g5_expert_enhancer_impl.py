"""
Tests for G5ExpertEnhancerImpl

Stage 3: Worker Profile-Driven Expert Execution Preparation

测试 G5 专家增强实现。
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, MagicMock, patch

from src.domain.models.fusion_result import Perspective
from src.domain.models.expert_context_pack import ExpertContextPack
from src.domain.models.llm_expert_perspective import LLMExpertPerspective
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.worker_profile import WorkerProfile
from src.domain.models.retrieval_mode import RetrievalMode
from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl


# =============================================================================
# Module-level Fixtures
# =============================================================================

@pytest.fixture
def mock_dependencies():
    """创建 mock 依赖"""
    gateway = Mock()
    retrieval = Mock()
    preparation = Mock()
    source = Mock()
    return gateway, retrieval, preparation, source


@pytest.fixture
def sample_profile():
    """创建示例 profile"""
    from src.domain.models.context_fragment import ContextFragment, ContextKind
    from src.domain.models.skill_profile import SkillProfile
    from src.domain.models.worker_profile import ProfileType

    return WorkerProfile(
        staff_id="001",
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_root="/path/to/profiles",
        context_fragments=[
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in Python and API design",
                source_path="/path/to/profiles/staff_001/default/openclaw/AGENTS.md",
            )
        ],
        active_skills=[
            SkillProfile(
                name="Python",
                description="Python programming",
                skill_id="skill_python_001",
                skill_set_name="programming",
            ),
            SkillProfile(
                name="FastAPI",
                description="FastAPI framework",
                skill_id="skill_fastapi_001",
                skill_set_name="web_frameworks",
            ),
        ],
    )


@pytest.fixture
def sample_digest():
    """创建示例 digest（含足够的上下文信息）"""
    from src.domain.models.context_fragment import ContextFragment, ContextKind

    fragment = ContextFragment(
        kind=ContextKind.AGENT,
        filename="AGENTS.md",
        content="Expert in Python and API design",
        source_path="/path/to/profiles/staff_001/default/openclaw/AGENTS.md",
    )

    return WorkerContextDigest(
        profile_key="staff_001:default",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        question="Test task context",
        relevant_fragments=[fragment],
        relevant_skills=[],
        context_summary="Expert in Python and API design",
        # 添加 sparse context 检测所需的属性
        total_fragments=1,
        total_skills=2,
        selected_fragments=1,
        selected_skills=0,
    )


# =============================================================================
# Test Classes
# =============================================================================

class FakeProfileSource:
    """测试用 Fake Profile Source"""

    def __init__(self, profiles: dict[str, WorkerProfile] | None = None):
        self._profiles = profiles or {}

    def get_by_id(self, profile_id: str) -> WorkerProfile | None:
        return self._profiles.get(profile_id)

    def list_all(self) -> list[WorkerProfile]:
        return list(self._profiles.values())


class TestG5ExpertEnhancerImplInit:
    """G5ExpertEnhancerImpl 初始化测试"""

    def test_init_with_required_dependencies(self):
        """测试初始化需要依赖"""
        mock_gateway = Mock()
        mock_retrieval = Mock()
        mock_preparation = Mock()
        mock_source = Mock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        assert enhancer is not None

    def test_init_with_optional_max_experts(self):
        """测试可选 max_experts 参数"""
        mock_gateway = Mock()
        mock_retrieval = Mock()
        mock_preparation = Mock()
        mock_source = Mock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
            max_experts=5,
        )

        assert enhancer is not None


class TestG5ExpertEnhancerImplEnhance:
    """G5ExpertEnhancerImpl enhance 方法测试"""

    def test_enhance_returns_perspectives(self, mock_dependencies):
        """测试 enhance 返回 Perspective 列表"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock 返回
        retrieval.retrieve.return_value = Mock(results=[])
        gateway.generate.return_value = Mock(
            parse_success=True,
            structured_data={
                "summary": "Expert perspective",
                "confidence": 0.85,
                "key_points": ["Point 1"],
                "concerns": ["Concern 1"],
                "risk_level": "low",
                "rationale_summary": "Based on expertise",
                "evidence_summary": ["Evidence 1"],
            }
        )

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

        assert isinstance(result, list)

    def test_enhance_uses_retrieval_service(self, mock_dependencies, sample_profile):
        """测试 enhance 使用 retrieval service"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock 返回
        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        from src.domain.models.retrieval_mode import RetrievalMode
        preparation.prepare.return_value = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Test",
            relevant_fragments=[],
            relevant_skills=[],
            context_summary="Expert",
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

        enhancer.enhance(
            question="How to design an API?",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 验证 retrieval 被调用
        retrieval.retrieve.assert_called_once()
        call_kwargs = retrieval.retrieve.call_args.kwargs
        assert call_kwargs["question"] == "How to design an API?"

    def test_enhance_uses_llm_gateway(self, mock_dependencies, sample_profile, sample_digest):
        """测试 enhance 使用 LLM gateway"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock 返回
        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
        gateway.generate.return_value = Mock(
            parse_success=True,
            structured_data={
                "summary": "Expert perspective on API design",
                "confidence": 0.90,
                "key_points": ["Use RESTful principles"],
                "concerns": [],
                "risk_level": "low",
                "rationale_summary": "Based on Python expertise",
                "evidence_summary": ["5 years experience"],
            }
        )

        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        result = enhancer.enhance(
            question="How to design an API?",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 验证 gateway 被调用
        gateway.generate.assert_called()
        assert len(result) > 0
        assert result[0].role == "expert"

    def test_enhance_fallback_on_llm_failure(self, mock_dependencies, sample_profile, sample_digest):
        """测试 LLM/gateway exception fallback（Layer 3c）"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock - LLM 失败
        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
        gateway.generate.side_effect = Exception("LLM failure")

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

        # 应该返回 fallback 视角，而不是抛出异常
        assert isinstance(result, list)
        assert len(result) > 0
        # fallback 视角应该有较低的置信度
        assert result[0].confidence is not None
        assert result[0].confidence < 0.8  # fallback 应该有较低置信度
        # 验证 concerns 包含 fallback 原因
        assert len(result[0].concerns) > 0
        assert "llm_exception" in result[0].concerns[0] or "fallback" in result[0].concerns[0].lower()

    def test_enhance_fallback_on_parse_failure(self, mock_dependencies, sample_profile, sample_digest):
        """测试 parse failure fallback（Layer 3b）"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock - parse 失败
        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
        gateway.generate.return_value = Mock(
            parse_success=False,
            structured_data=None,
            raw_text="Invalid JSON",
        )

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
        assert isinstance(result, list)
        assert len(result) > 0
        # 验证 concerns 包含 parse_failure 原因
        assert len(result[0].concerns) > 0
        assert "parse_failure" in result[0].concerns[0]

    def test_fallback_does_not_interrupt_main_flow(self, mock_dependencies, sample_profile, sample_digest):
        """测试 fallback 不中断 G5 主流程"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock - 第一个成功，第二个失败
        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9),
            Mock(profile=sample_profile, total_score=0.8),
        ])
        preparation.prepare.return_value = sample_digest

        # 第一次成功，第二次失败
        gateway.generate.side_effect = [
            Mock(
                parse_success=True,
                structured_data={
                    "summary": "Expert perspective 1",
                    "confidence": 0.85,
                    "key_points": [],
                    "concerns": [],
                    "risk_level": "low",
                    "rationale_summary": "Based on expertise",
                    "evidence_summary": [],
                }
            ),
            Exception("LLM failure on second call"),
        ]

        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
            max_experts=2,
        )

        result = enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001", "staff_002"],
        )

        # 应该返回 2 个视角（一个成功，一个 fallback）
        assert len(result) == 2
        # 第一个是成功的
        assert result[0].confidence == 0.85
        # 第二个是 fallback
        assert result[1].confidence < 0.8

    def test_enhance_returns_base_perspectives_when_no_profiles(self, mock_dependencies):
        """测试无 profile 时返回 base perspectives"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock - 无 profile
        retrieval.retrieve.return_value = Mock(results=[])

        base_perspectives = [
            Perspective(
                participant_id="staff_001",
                participant_type="bot",
                role="consultant",
                summary="Base perspective",
                status="completed",
            )
        ]

        enhancer = G5ExpertEnhancerImpl(
            gateway=gateway,
            retrieval_service=retrieval,
            preparation_service=preparation,
            profile_source=source,
        )

        result = enhancer.enhance(
            question="Test question",
            base_perspectives=base_perspectives,
            participants=["staff_001"],
        )

        # 无 profile 时应该返回 base perspectives
        assert result == base_perspectives

    def test_enhance_respects_max_experts(self, mock_dependencies, sample_profile, sample_digest):
        """测试 max_experts 限制"""
        gateway, retrieval, preparation, source = mock_dependencies

        # 配置 mock - 返回多个 profile
        profiles = [
            Mock(profile=sample_profile, total_score=0.9),
            Mock(profile=sample_profile, total_score=0.8),
            Mock(profile=sample_profile, total_score=0.7),
        ]
        retrieval.retrieve.return_value = Mock(results=profiles)
        preparation.prepare.return_value = sample_digest
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
            max_experts=2,  # 限制为 2 个
        )

        result = enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001", "staff_002", "staff_003"],
        )

        # 验证最多生成 max_experts 个视角
        assert len(result) <= 2

    def test_enhance_with_driver_bot_id(self, mock_dependencies, sample_profile, sample_digest):
        """测试 driver_bot_id 参数传递"""
        gateway, retrieval, preparation, source = mock_dependencies

        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
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

        enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001"],
            driver_bot_id="staff_001:default",
        )

        # 验证参数被传递（不会抛出异常）


class TestG5ExpertEnhancerImplExpertContextPack:
    """G5ExpertEnhancerImpl ExpertContextPack 构建测试"""

    def test_build_expert_context_pack(self):
        """测试构建 ExpertContextPack"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # 使用 mock 依赖
        mock_gateway = Mock()
        mock_retrieval = Mock()
        mock_preparation = Mock()
        mock_source = Mock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Task context",
            relevant_fragments=[],
            relevant_skills=[],
            context_summary="Expert in Python",
        )

        pack = enhancer._build_expert_context_pack(
            question="How to design an API?",
            digest=digest,
            domain="tech",
        )

        assert isinstance(pack, ExpertContextPack)
        assert pack.question == "How to design an API?"
        assert pack.expert_id == "staff_001:default"
        assert "staff_001" in pack.profile_key
        assert pack.domain == "tech"


class TestG5ExpertEnhancerImplFallbackPerspective:
    """G5ExpertEnhancerImpl fallback 视角生成测试"""

    def test_generate_fallback_perspective(self):
        """测试生成 fallback 视角"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        mock_gateway = Mock()
        mock_retrieval = Mock()
        mock_preparation = Mock()
        mock_source = Mock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        pack = ExpertContextPack(
            question="Test question",
            expert_id="staff_001:default",
            profile_key="staff_001:default:test",
            domain="tech",
            expertise_summary="Expert in Python",
            relevant_skills=["Python"],
            context_highlights=["5 years experience"],
            task_context="Task context",
        )

        perspective = enhancer._generate_fallback_perspective(pack)

        assert isinstance(perspective, Perspective)
        assert perspective.participant_id == "staff_001:default"
        assert perspective.role == "expert"
        assert perspective.status == "completed"
        assert perspective.confidence is not None
        assert perspective.confidence < 0.8  # fallback 应该有较低置信度


class TestG5ExpertEnhancerImplTraceability:
    """G5ExpertEnhancerImpl traceability 测试"""

    def test_profile_key_preserved_in_context_pack(self):
        """测试 profile_key 在 context pack 中保留"""
        mock_gateway = Mock()
        mock_retrieval = Mock()
        mock_preparation = Mock()
        mock_source = Mock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="Task context",
            relevant_fragments=[],
            relevant_skills=[],
            context_summary="Expert in Python",
        )

        pack = enhancer._build_expert_context_pack(
            question="Test question",
            digest=digest,
            domain="tech",
        )

        # profile_key 应包含 profile_id 信息
        assert "staff_001:default" in pack.profile_key
        # profile_key 格式应为 profile_id:timestamp
        parts = pack.profile_key.split(":")
        assert len(parts) >= 2

    def test_expert_id_preserved_in_perspective(self, mock_dependencies, sample_profile, sample_digest):
        """测试 expert_id 在 perspective 中保留"""
        gateway, retrieval, preparation, source = mock_dependencies

        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
        gateway.generate.return_value = Mock(
            parse_success=True,
            structured_data={
                "summary": "Expert perspective",
                "confidence": 0.85,
                "key_points": ["Point 1"],
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

        result = enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # perspective.participant_id 应等于 profile_id
        assert len(result) > 0
        assert result[0].participant_id == "staff_001:default"

    def test_profile_key_logged_during_generation(self, mock_dependencies, sample_profile, sample_digest, caplog):
        """测试 profile_key 在生成过程中被记录到日志"""
        import logging

        # 设置日志级别
        caplog.set_level(logging.INFO)

        gateway, retrieval, preparation, source = mock_dependencies

        retrieval.retrieve.return_value = Mock(results=[
            Mock(profile=sample_profile, total_score=0.9)
        ])
        preparation.prepare.return_value = sample_digest
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

        enhancer.enhance(
            question="Test question",
            base_perspectives=[],
            participants=["staff_001"],
        )

        # 检查日志中包含 profile_key 信息
        log_messages = caplog.text
        assert "profile_key" in log_messages or "staff_001" in log_messages


# =============================================================================
# Stage 4 Phase 3: G5 real-context deepening tests
# =============================================================================

class TestG5ExpertEnhancerImplDomainInference:
    """G5ExpertEnhancerImpl domain 推断测试 (Phase 3)"""

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
    def profile_with_security_skills(self):
        """创建有 security 技能的 profile"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile
        from src.domain.models.worker_profile import ProfileType

        return WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[],
            active_skills=[
                SkillProfile(
                    name="Security",
                    description="Security architecture and penetration testing",
                    skill_id="skill_sec_001",
                    skill_set_name="security",
                ),
                SkillProfile(
                    name="Authentication",
                    description="OAuth and JWT authentication",
                    skill_id="skill_auth_001",
                    skill_set_name="security",
                ),
            ],
        )

    @pytest.fixture
    def profile_with_legal_context(self):
        """创建有 legal context 的 profile"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile
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
                    content="Expert in legal compliance, GDPR, and data privacy regulations.",
                    source_path="/test/profiles/staff_002/default/openclaw/AGENTS.md",
                ),
            ],
            active_skills=[],
        )

    @pytest.fixture
    def profile_with_weak_signals(self):
        """创建弱信号的 profile（无明确领域）"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind
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
                    content="General purpose assistant.",
                    source_path="/test/profiles/staff_003/default/openclaw/AGENTS.md",
                ),
            ],
            active_skills=[],
        )

    def test_infer_domain_from_active_skills(self, enhancer, profile_with_security_skills):
        """测试从 active_skills 推断领域"""
        domain = enhancer._infer_domain_from_profile(profile_with_security_skills)
        assert domain == "security"

    def test_infer_domain_from_context_fragments(self, enhancer, profile_with_legal_context):
        """测试从 context_fragments 推断领域"""
        domain = enhancer._infer_domain_from_profile(profile_with_legal_context)
        assert domain == "legal"

    def test_infer_domain_prefers_skills_over_weak_context(self, enhancer):
        """测试当 skills 更明确时优先使用 skills 推断"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile
        from src.domain.models.worker_profile import ProfileType

        # 技能说 database，context 说 general
        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
            context_fragments=[
                ContextFragment(
                    kind=ContextKind.AGENT,
                    filename="AGENTS.md",
                    content="General technical work",
                    source_path="/test/AGENTS.md",
                ),
            ],
            active_skills=[
                SkillProfile(
                    name="Database",
                    description="Database optimization",
                    skill_id="skill_db_001",
                    skill_set_name="database",
                ),
            ],
        )

        domain = enhancer._infer_domain_from_profile(profile)
        # 技能应该优先
        assert domain == "database"

    def test_infer_domain_falls_back_to_general_when_uncertain(self, enhancer, profile_with_weak_signals):
        """测试当无法确定领域时回退到 general"""
        domain = enhancer._infer_domain_from_profile(profile_with_weak_signals)
        # 应该回退到 general
        assert domain in ["general", "tech"]


class TestG5ExpertEnhancerImplContextScoring:
    """G5ExpertEnhancerImpl context 评分测试 (Phase 3)"""

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
    def sample_fragments(self):
        """创建示例 fragments"""
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        return [
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content="Expert in security architecture and penetration testing.",
                source_path="/test/AGENTS.md",
            ),
            ContextFragment(
                kind=ContextKind.SOUL,
                filename="SOUL.md",
                content="General purpose guidance and principles.",
                source_path="/test/SOUL.md",
            ),
            ContextFragment(
                kind=ContextKind.AGENT,
                filename="TOOLS.md",
                content="Uses security tools like Burp Suite and OWASP ZAP.",
                source_path="/test/TOOLS.md",
            ),
        ]

    def test_score_context_fragments_returns_scores(self, enhancer, sample_fragments):
        """测试评分 fragments 返回分数"""
        question = "How to perform security testing?"
        scores = enhancer._score_context_fragments(sample_fragments, question)

        assert isinstance(scores, dict)
        assert len(scores) == len(sample_fragments)
        # 所有分数应该在 0-1 之间
        for score in scores.values():
            assert 0 <= score <= 1

    def test_score_context_fragments_relevance_ordering(self, enhancer, sample_fragments):
        """测试与问题相关的 fragment 得分更高"""
        question = "How to perform security testing?"
        scores = enhancer._score_context_fragments(sample_fragments, question)

        # 与 security 相关的 fragment 应该得分更高
        # AGENTS.md 包含 "security"，应该得分高
        agents_score = scores.get("AGENTS.md", 0)
        soul_score = scores.get("SOUL.md", 0)

        # AGENTS.md 应该比 SOUL.md 得分高（因为包含 security 关键词）
        assert agents_score >= soul_score


class TestG5ExpertEnhancerImplSkillScoring:
    """G5ExpertEnhancerImpl skill 评分测试 (Phase 3)"""

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
    def sample_skills(self):
        """创建示例 skills"""
        from src.domain.models.skill_profile import SkillProfile
        return [
            SkillProfile(
                name="Security",
                description="Security architecture and testing",
                skill_id="skill_sec_001",
                skill_set_name="security",
            ),
            SkillProfile(
                name="Python",
                description="Python programming",
                skill_id="skill_py_001",
                skill_set_name="programming",
            ),
            SkillProfile(
                name="Penetration Testing",
                description="Web application security testing",
                skill_id="skill_pentest_001",
                skill_set_name="security",
            ),
        ]

    def test_score_skills_returns_scores(self, enhancer, sample_skills):
        """测试评分 skills 返回分数"""
        question = "How to perform security testing?"
        scores = enhancer._score_skills(sample_skills, question)

        assert isinstance(scores, dict)
        assert len(scores) == len(sample_skills)
        for score in scores.values():
            assert 0 <= score <= 1

    def test_score_skills_relevance_ordering(self, enhancer, sample_skills):
        """测试与问题相关的 skill 得分更高"""
        question = "How to perform security testing?"
        scores = enhancer._score_skills(sample_skills, question)

        # Security 技能应该得分高
        security_score = scores.get("Security", 0)
        python_score = scores.get("Python", 0)

        # Security 应该比 Python 得分高（因为问题包含 security）
        assert security_score >= python_score


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
        assert perspective.participant_id == "staff_001:default"

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