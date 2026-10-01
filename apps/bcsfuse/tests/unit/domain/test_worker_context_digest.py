"""
Tests for Worker Context Digest

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：
- WorkerContextDigest: Task-specific 上下文摘要模型
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestWorkerContextDigest:
    """测试 WorkerContextDigest 模型"""

    def test_create_context_digest_success(self):
        """测试创建上下文摘要"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile

        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="# Identity\nName: Test Bot",
            source_path="/test/SOUL.md",
        )

        skill = SkillProfile(
            name="web_search",
            skill_id="search_v1",
            skill_set_name="default",
        )

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="How to search the web?",
            context_summary="Expert in web search capability",
            relevant_fragments=[fragment],
            relevant_skills=[skill],
            fragment_scores={"SOUL.md": 0.8},
            skill_scores={"web_search": 0.9},
            reasons=["Context matches query keywords", "Skill directly relevant"],
            total_fragments=3,
            selected_fragments=1,
            total_skills=5,
            selected_skills=1,
        )

        assert digest.profile_key == "staff_001:default"
        assert digest.mode == RetrievalMode.AGENT
        assert digest.question == "How to search the web?"
        assert len(digest.relevant_fragments) == 1
        assert len(digest.relevant_skills) == 1
        assert digest.context_summary == "Expert in web search capability"

    def test_create_context_digest_minimal(self):
        """测试创建最小字段上下文摘要"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.GENERAL,
            question="test question",
            context_summary="test summary",
        )

        assert digest.profile_key == "staff_001:default"
        assert digest.relevant_fragments == []
        assert digest.relevant_skills == []
        assert digest.fragment_scores == {}
        assert digest.skill_scores == {}
        assert digest.reasons == []
        assert digest.total_fragments == 0
        assert digest.selected_fragments == 0
        assert digest.total_skills == 0
        assert digest.selected_skills == 0

    def test_fragment_scores_dict(self):
        """测试片段评分字典"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="test",
            context_summary="test",
            fragment_scores={
                "SOUL.md": 0.9,
                "AGENTS.md": 0.7,
                "RULES.md": 0.3,
            },
        )

        assert digest.fragment_scores["SOUL.md"] == 0.9
        assert digest.fragment_scores["AGENTS.md"] == 0.7
        assert len(digest.fragment_scores) == 3

    def test_skill_scores_dict(self):
        """测试技能评分字典"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="test",
            context_summary="test",
            skill_scores={
                "web_search": 0.95,
                "data_analysis": 0.6,
            },
        )

        assert digest.skill_scores["web_search"] == 0.95
        assert len(digest.skill_scores) == 2

    def test_selection_statistics(self):
        """测试选择统计字段"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            question="test",
            context_summary="test",
            total_fragments=10,
            selected_fragments=3,
            total_skills=8,
            selected_skills=2,
        )

        # 计算选择比例
        assert digest.fragment_selection_ratio == 0.3  # 3/10
        assert digest.skill_selection_ratio == 0.25  # 2/8

    def test_selection_statistics_zero_total(self):
        """测试总数为零时的统计"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.GENERAL,
            question="test",
            context_summary="test",
            total_fragments=0,
            selected_fragments=0,
            total_skills=0,
            selected_skills=0,
        )

        # 除零保护
        assert digest.fragment_selection_ratio == 0.0
        assert digest.skill_selection_ratio == 0.0

    def test_missing_required_fields_raises_error(self):
        """测试缺少必填字段抛出错误"""
        from src.domain.models.worker_context_digest import WorkerContextDigest

        with pytest.raises(ValidationError):
            WorkerContextDigest(
                mode="agent",
                question="test",
                context_summary="test",
            )

        with pytest.raises(ValidationError):
            WorkerContextDigest(
                profile_key="staff_001:default",
                question="test",
                context_summary="test",
            )

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        with pytest.raises(ValidationError):
            WorkerContextDigest(
                profile_key="staff_001:default",
                mode=RetrievalMode.AGENT,
                question="test",
                context_summary="test",
                extra_field="not_allowed",  # type: ignore
            )

    def test_mode_different_values(self):
        """测试不同模式"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        for mode in [RetrievalMode.AGENT, RetrievalMode.CONFLICT_ALIGNMENT,
                     RetrievalMode.EXPERT_DIAGNOSIS, RetrievalMode.GENERAL]:
            digest = WorkerContextDigest(
                profile_key="staff_001:default",
                mode=mode,
                question="test",
                context_summary="test",
            )
            assert digest.mode == mode

    def test_has_relevant_content(self):
        """测试是否有相关内容"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile

        # 有 fragments
        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="test",
            context_summary="test",
            relevant_fragments=[
                ContextFragment(
                    kind=ContextKind.SOUL,
                    filename="SOUL.md",
                    content="test",
                    source_path="/test",
                )
            ],
        )
        assert digest.has_relevant_content is True

        # 有 skills
        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="test",
            context_summary="test",
            relevant_skills=[
                SkillProfile(
                    name="test_skill",
                    skill_id="v1",
                    skill_set_name="default",
                )
            ],
        )
        assert digest.has_relevant_content is True

        # 都没有
        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="test",
            context_summary="test",
        )
        assert digest.has_relevant_content is False


class TestWorkerContextDigestReasons:
    """测试 reasons 字段"""

    def test_reasons_list(self):
        """测试原因列表"""
        from src.domain.models.worker_context_digest import WorkerContextDigest
        from src.domain.models.retrieval_mode import RetrievalMode

        digest = WorkerContextDigest(
            profile_key="staff_001:default",
            mode=RetrievalMode.AGENT,
            question="How to search?",
            context_summary="test",
            reasons=[
                "Context fragment 'SOUL.md' matches query keywords",
                "Skill 'web_search' is directly relevant",
                "Domain 'search' is related to question",
            ],
        )

        assert len(digest.reasons) == 3
        assert "web_search" in digest.reasons[1]