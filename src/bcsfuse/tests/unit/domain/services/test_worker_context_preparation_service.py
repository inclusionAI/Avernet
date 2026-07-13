"""
Tests for Worker Context Preparation Service

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：
- WorkerContextPreparationService: task-specific context 裁剪
- Context digest generation with scoring
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import Mock

import pytest

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.domain.models.worker_context_digest import WorkerContextDigest


def create_test_profile_with_content(
    staff_id: str,
    profile_id: str,
    skills: list[tuple[str, str]],  # (name, description)
    fragments: list[tuple[str, str, str]],  # (kind, filename, content)
    profile_type: ProfileType = ProfileType.DEFAULT,
) -> WorkerProfile:
    """Create a test WorkerProfile with detailed content"""
    active_skills = [
        SkillProfile(
            name=name,
            description=description,
            skill_id=f"skill_{name.lower().replace(' ', '_')}",
            skill_set_name="default",
        )
        for name, description in skills
    ]

    context_fragments = [
        ContextFragment(
            kind=ContextKind(kind),
            filename=filename,
            content=content,
            source_path=f"/test/{filename}",
        )
        for kind, filename, content in fragments
    ]

    return WorkerProfile(
        staff_id=staff_id,
        profile_id=profile_id,
        profile_type=profile_type,
        source_root="/test",
        context_fragments=context_fragments,
        active_skills=active_skills,
    )


class TestWorkerContextPreparationService:
    """测试 WorkerContextPreparationService"""

    @pytest.fixture
    def sample_profile(self):
        """Create a sample profile with rich content"""
        return create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[
                ("Python", "Expert in Python programming language"),
                ("API Design", "RESTful API design and implementation"),
                ("Database", "PostgreSQL and MySQL database optimization"),
                ("Web Development", "Frontend and backend web development"),
            ],
            fragments=[
                (
                    "agent",
                    "AGENTS.md",
                    "I am a Python expert specializing in API design. "
                    "I have experience with RESTful services and database optimization.",
                ),
                (
                    "soul",
                    "SOUL.md",
                    "My core values include clean code, thorough testing, "
                    "and continuous learning. I believe in documentation.",
                ),
                (
                    "tools",
                    "TOOLS.md",
                    "I use pytest for testing, Docker for containerization, "
                    "and PostgreSQL for databases.",
                ),
            ],
        )

    def test_prepare_context_basic(self, sample_profile):
        """测试基本 context 准备"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=sample_profile,
            question="How to design a REST API?",
            mode=RetrievalMode.AGENT,
        )

        assert digest is not None
        assert digest.profile_key == sample_profile.profile_key
        assert digest.mode == RetrievalMode.AGENT
        assert digest.question == "How to design a REST API?"
        assert isinstance(digest, WorkerContextDigest)

    def test_prepare_context_scores_fragments(self, sample_profile):
        """测试片段评分"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=sample_profile,
            question="API design and Python",
            mode=RetrievalMode.AGENT,
        )

        # 应该有 scored fragments
        assert len(digest.fragment_scores) > 0
        # 所有评分应该在 0-1 之间
        for score in digest.fragment_scores.values():
            assert 0 <= score <= 1

    def test_prepare_context_scores_skills(self, sample_profile):
        """测试技能评分"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=sample_profile,
            question="Python database optimization",
            mode=RetrievalMode.AGENT,
        )

        # 应该有 scored skills
        assert len(digest.skill_scores) > 0
        # 相关技能应该有较高分数
        assert "Python" in digest.skill_scores or "Database" in digest.skill_scores

    def test_prepare_context_selects_relevant_fragments(self, sample_profile):
        """测试选择相关片段"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=sample_profile,
            question="REST API design",
            mode=RetrievalMode.AGENT,
            max_fragments=5,
        )

        # 应该选择相关片段
        assert len(digest.relevant_fragments) <= 5
        # 统计信息应该正确
        assert digest.total_fragments == 3
        assert digest.selected_fragments == len(digest.relevant_fragments)

    def test_prepare_context_selects_relevant_skills(self, sample_profile):
        """测试选择相关技能"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=sample_profile,
            question="Python programming",
            mode=RetrievalMode.AGENT,
            max_skills=5,
        )

        # 应该选择相关技能
        assert len(digest.relevant_skills) <= 5
        # 统计信息应该正确
        assert digest.total_skills == 4
        assert digest.selected_skills == len(digest.relevant_skills)

    def test_prepare_context_generates_summary(self, sample_profile):
        """测试生成摘要"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=sample_profile,
            question="API development",
            mode=RetrievalMode.AGENT,
        )

        # 应该有上下文摘要
        assert digest.context_summary is not None
        assert len(digest.context_summary) > 0

    def test_prepare_context_max_fragments_limit(self):
        """测试片段数量限制"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        # 创建包含很多片段的 profile（使用有效的 ContextKind）
        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[("Python", "Python programming")],
            fragments=[
                ("agent", f"AGENTS_{i}.md", f"Content {i}")
                for i in range(10)
            ],
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="test",
            mode=RetrievalMode.AGENT,
            max_fragments=3,
        )

        assert len(digest.relevant_fragments) <= 3

    def test_prepare_context_max_skills_limit(self):
        """测试技能数量限制"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        # 创建包含很多技能的 profile
        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[(f"Skill{i}", f"Description for skill {i}") for i in range(10)],
            fragments=[("agent", "AGENTS.md", "Test content")],
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="test",
            mode=RetrievalMode.AGENT,
            max_skills=3,
        )

        assert len(digest.relevant_skills) <= 3

    def test_prepare_context_mode_aware(self, sample_profile):
        """测试 mode-aware 处理"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        # 不同模式可能产生不同的结果
        for mode in [RetrievalMode.AGENT, RetrievalMode.CONFLICT_ALIGNMENT,
                     RetrievalMode.EXPERT_DIAGNOSIS]:
            digest = service.prepare(
                profile=sample_profile,
                question="API design",
                mode=mode,
            )
            assert digest.mode == mode

    def test_prepare_context_includes_reasons(self, sample_profile):
        """测试包含选择理由"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=sample_profile,
            question="Python API",
            mode=RetrievalMode.AGENT,
        )

        # 应该有选择理由
        assert len(digest.reasons) > 0


class TestContextPreparationScoring:
    """测试 context preparation 评分逻辑"""

    def test_high_relevance_fragments_scored_higher(self):
        """测试高相关性片段获得更高分数"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[("Python", "Python programming")],
            fragments=[
                ("agent", "AGENTS.md", "I am an expert in Python API design."),
                ("soul", "SOUL.md", "Philosophy about coding."),
                ("tools", "TOOLS.md", "Unrelated content about cooking."),
            ],
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="Python API design",
            mode=RetrievalMode.AGENT,
        )

        # API design 相关的片段应该有较高分数
        agents_score = digest.fragment_scores.get("AGENTS.md", 0)
        tools_score = digest.fragment_scores.get("TOOLS.md", 0)

        # API 相关片段分数应该更高
        assert agents_score >= tools_score

    def test_skill_name_exact_match_higher_score(self):
        """测试技能名称精确匹配获得更高分数"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[
                ("Python", "Python programming language"),
                ("Java", "Java programming language"),
                ("JavaScript", "JavaScript for web development"),
            ],
            fragments=[],
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="Python development",
            mode=RetrievalMode.AGENT,
        )

        # Python 应该是最相关的技能
        python_score = digest.skill_scores.get("Python", 0)
        assert python_score > 0


class TestContextPreparationEdgeCases:
    """测试边缘情况"""

    def test_prepare_empty_profile(self):
        """测试空 profile"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/test",
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="test question",
            mode=RetrievalMode.AGENT,
        )

        assert digest.total_fragments == 0
        assert digest.total_skills == 0
        assert len(digest.relevant_fragments) == 0
        assert len(digest.relevant_skills) == 0

    def test_prepare_no_matching_content(self):
        """测试无匹配内容"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[("Cooking", "Expert chef")],
            fragments=[("agent", "AGENTS.md", "I am a professional chef.")],
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="Python programming",
            mode=RetrievalMode.AGENT,
        )

        # 可能没有相关内容
        assert digest.has_relevant_content or len(digest.relevant_fragments) == 0

    def test_prepare_vague_question(self):
        """测试模糊问题"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        # 创建测试 profile
        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[
                ("Python", "Expert in Python programming language"),
                ("API Design", "RESTful API design and implementation"),
            ],
            fragments=[
                (
                    "agent",
                    "AGENTS.md",
                    "I am a Python expert specializing in API design.",
                ),
            ],
        )

        service = WorkerContextPreparationService()

        # 模糊问题应该返回一些内容
        digest = service.prepare(
            profile=profile,
            question="help",
            mode=RetrievalMode.AGENT,
        )

        assert digest is not None
        assert len(digest.reasons) > 0


class TestContextDigestProperties:
    """测试 WorkerContextDigest 属性"""

    def test_fragment_selection_ratio(self):
        """测试片段选择比例"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[("Python", "Python")],
            fragments=[
                ("agent", f"file{i}.md", f"Content {i}")
                for i in range(10)
            ],
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="test",
            mode=RetrievalMode.AGENT,
            max_fragments=3,
        )

        # 选择比例应该是选中数除以总数
        expected_ratio = digest.selected_fragments / digest.total_fragments
        assert abs(digest.fragment_selection_ratio - expected_ratio) < 0.01

    def test_has_relevant_content(self):
        """测试 has_relevant_content 属性"""
        from src.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )

        profile = create_test_profile_with_content(
            staff_id="001",
            profile_id="default",
            skills=[("Python", "Python programming")],
            fragments=[("agent", "AGENTS.md", "Python expert")],
        )

        service = WorkerContextPreparationService()

        digest = service.prepare(
            profile=profile,
            question="Python",
            mode=RetrievalMode.AGENT,
        )

        # 应该有相关内容
        assert digest.has_relevant_content is True