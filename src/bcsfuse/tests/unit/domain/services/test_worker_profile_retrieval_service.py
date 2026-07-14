"""
Tests for Worker Profile Retrieval Service

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：
- WorkerProfileRetrievalService: mode-aware 检索
- ScoringStrategy: 不同模式的评分策略
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import Mock

import pytest

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.scoring_signal import ScoringSignal, SignalType
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import WorkerProfile, ProfileType


class MockWorkerProfileSource:
    """Mock WorkerProfileSource for testing"""

    def __init__(self, profiles: list[WorkerProfile]):
        self._profiles = profiles

    def scan(self):
        """Scan all profiles"""
        from src.domain.models.worker_profile import WorkerProfileScanResult
        return WorkerProfileScanResult(profiles=self._profiles)

    def get_profile(self, staff_id: str, profile_id: str) -> Optional[WorkerProfile]:
        """Get profile by staff_id and profile_id"""
        for p in self._profiles:
            if p.staff_id == staff_id and p.profile_id == profile_id:
                return p
        return None

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        """Get profiles by staff_id"""
        return [p for p in self._profiles if p.staff_id == staff_id]


def create_test_profile(
    staff_id: str,
    profile_id: str,
    skills: list[str],
    context_kinds: Optional[list[str]] = None,
    profile_type: ProfileType = ProfileType.DEFAULT,
) -> WorkerProfile:
    """Create a test WorkerProfile

    Args:
        staff_id: 员工 ID
        profile_id: 画像 ID
        skills: 技能名称列表
        context_kinds: 上下文类型列表（使用 ContextKind 的值，如 "agent", "soul"）
        profile_type: 画像类型
    """
    active_skills = [
        SkillProfile(
            name=name,
            description=f"Description for {name}",
            skill_id=f"skill_{name.lower()}",
            skill_set_name="default",
        )
        for name in skills
    ]

    context_fragments = []
    if context_kinds:
        for kind in context_kinds:
            context_fragments.append(
                ContextFragment(
                    kind=ContextKind(kind),
                    filename=f"{kind}.md",
                    content=f"Content for {kind} about {', '.join(skills)}",
                    source_path=f"/test/{kind}.md",
                )
            )

    return WorkerProfile(
        staff_id=staff_id,
        profile_id=profile_id,
        profile_type=profile_type,
        source_root="/test",
        context_fragments=context_fragments,
        active_skills=active_skills,
    )


class TestRetrievalResult:
    """测试 RetrievalResult 模型"""

    def test_create_retrieval_result_success(self):
        """测试创建检索结果"""
        from src.domain.services.worker_profile_retrieval_service import RetrievalResult

        profile = create_test_profile(
            staff_id="001",
            profile_id="default",
            skills=["Python", "API"],
            context_kinds=["agent"],
        )

        signals = [
            ScoringSignal(
                signal_type=SignalType.SKILL_NAME_MATCH,
                raw_score=1.0,
                weight=0.3,
                details={"skill": "Python"},
            )
        ]

        result = RetrievalResult(
            profile=profile,
            total_score=0.85,
            signals=signals,
            rank=1,
        )

        assert result.profile.staff_id == "001"
        assert result.total_score == 0.85
        assert len(result.signals) == 1
        assert result.rank == 1

    def test_retrieval_result_with_multiple_signals(self):
        """测试带多个信号的检索结果"""
        from src.domain.services.worker_profile_retrieval_service import RetrievalResult

        profile = create_test_profile(
            staff_id="002",
            profile_id="default",
            skills=["Java", "Database"],
            context_kinds=["soul"],
        )

        signals = [
            ScoringSignal(
                signal_type=SignalType.SKILL_NAME_MATCH,
                raw_score=0.8,
                weight=0.3,
                details={"skill": "Java"},
            ),
            ScoringSignal(
                signal_type=SignalType.CONTEXT_MATCH,
                raw_score=0.6,
                weight=0.2,
                details={"fragment": "soul"},
            ),
            ScoringSignal(
                signal_type=SignalType.COVERAGE_SCORE,
                raw_score=0.9,
                weight=0.1,
                details={"type": "domain_diversity"},
            ),
        ]

        result = RetrievalResult(
            profile=profile,
            total_score=0.75,
            signals=signals,
            rank=2,
        )

        assert len(result.signals) == 3
        # Verify weighted scores are calculated (use approximate comparison for floats)
        assert abs(result.signals[0].weighted_score - 0.24) < 0.001
        assert abs(result.signals[1].weighted_score - 0.12) < 0.001
        assert abs(result.signals[2].weighted_score - 0.09) < 0.001


class TestWorkerProfileRetrievalService:
    """测试 WorkerProfileRetrievalService"""

    @pytest.fixture
    def mock_source(self):
        """Create mock source with test profiles"""
        profiles = [
            create_test_profile(
                staff_id="001",
                profile_id="default",
                skills=["Python", "Web Development", "API Design"],
                context_kinds=["agent", "soul"],
            ),
            create_test_profile(
                staff_id="002",
                profile_id="default",
                skills=["Java", "Database", "System Design"],
                context_kinds=["agent", "other"],
            ),
            create_test_profile(
                staff_id="003",
                profile_id="default",
                skills=["Python", "Machine Learning", "Data Science"],
                context_kinds=["soul", "other"],
            ),
        ]
        return MockWorkerProfileSource(profiles)

    def test_retrieve_profiles_basic(self, mock_source):
        """测试基本检索功能"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="I need help with Python API development",
            mode=RetrievalMode.AGENT,
            top_k=5,
        )

        assert result is not None
        assert len(result.results) > 0
        assert all(r.total_score >= 0 for r in result.results)

    def test_retrieve_profiles_returns_sorted_by_score(self, mock_source):
        """测试结果按分数排序"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="Python Machine Learning",
            mode=RetrievalMode.AGENT,
            top_k=5,
        )

        scores = [r.total_score for r in result.results]
        assert scores == sorted(scores, reverse=True)

    def test_retrieve_profiles_respects_top_k(self, mock_source):
        """测试 top_k 限制"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="programming",
            mode=RetrievalMode.AGENT,
            top_k=2,
        )

        assert len(result.results) <= 2

    def test_retrieve_mode_agent(self, mock_source):
        """测试 AGENT 模式检索"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="Python API",
            mode=RetrievalMode.AGENT,
            top_k=10,
        )

        # G1 应该关注直接相关性
        assert result.mode == RetrievalMode.AGENT
        assert len(result.results) > 0

    def test_retrieve_mode_conflict_alignment(self, mock_source):
        """测试 CONFLICT_ALIGNMENT 模式检索"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="architecture decision",
            mode=RetrievalMode.CONFLICT_ALIGNMENT,
            top_k=10,
        )

        assert result.mode == RetrievalMode.CONFLICT_ALIGNMENT
        assert len(result.results) > 0

    def test_retrieve_mode_expert_diagnosis(self, mock_source):
        """测试 EXPERT_DIAGNOSIS 模式检索"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="system health check",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            top_k=10,
        )

        # G5 应该优先领域覆盖/多样性
        assert result.mode == RetrievalMode.EXPERT_DIAGNOSIS
        assert len(result.results) > 0

    def test_retrieve_generates_scoring_signals(self, mock_source):
        """测试生成评分信号"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="Python API",
            mode=RetrievalMode.AGENT,
            top_k=10,
        )

        # 每个结果都应该有评分信号
        for r in result.results:
            assert len(r.signals) > 0
            for signal in r.signals:
                assert signal.raw_score >= 0
                assert signal.weight >= 0
                assert signal.weighted_score is not None

    def test_retrieve_with_skill_filter(self, mock_source):
        """测试基于技能过滤"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="Python",
            mode=RetrievalMode.AGENT,
            top_k=10,
            skill_filter=["Python"],
        )

        # 所有结果都应该有 Python 技能
        for r in result.results:
            skill_names = [s.name for s in r.profile.active_skills]
            assert "Python" in skill_names

    def test_retrieve_with_profile_keys(self, mock_source):
        """测试指定 profile keys"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="programming",
            mode=RetrievalMode.AGENT,
            top_k=10,
            profile_keys=["staff_001:default"],
        )

        # 只返回指定的 profile
        assert len(result.results) == 1
        assert result.results[0].profile.staff_id == "001"


class TestModeAwareScoring:
    """测试 mode-aware 评分"""

    @pytest.fixture
    def diverse_source(self):
        """Create source with diverse profiles for G5 testing"""
        profiles = [
            # Frontend 专家
            create_test_profile(
                staff_id="001",
                profile_id="default",
                skills=["React", "TypeScript", "CSS"],
                context_kinds=["agent"],
            ),
            # Backend 专家
            create_test_profile(
                staff_id="002",
                profile_id="default",
                skills=["Python", "Django", "PostgreSQL"],
                context_kinds=["agent"],
            ),
            # DevOps 专家
            create_test_profile(
                staff_id="003",
                profile_id="default",
                skills=["Kubernetes", "Docker", "CI/CD"],
                context_kinds=["agent"],
            ),
            # Data 专家
            create_test_profile(
                staff_id="004",
                profile_id="default",
                skills=["Python", "Spark", "Machine Learning"],
                context_kinds=["agent"],
            ),
            # Security 专家
            create_test_profile(
                staff_id="005",
                profile_id="default",
                skills=["Security", "Encryption", "Authentication"],
                context_kinds=["agent"],
            ),
        ]
        return MockWorkerProfileSource(profiles)

    def test_g5_prioritizes_domain_diversity(self, diverse_source):
        """测试 G5 优先领域多样性"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=diverse_source)

        result = service.retrieve(
            question="Python system development",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            top_k=3,
        )

        # G5 应该返回多样化的专家
        # 即使问题提到 Python，也应该包含其他领域专家
        assert len(result.results) > 0

        # 检查结果是多样化的（不同领域的技能）
        all_skills = set()
        for r in result.results:
            for skill in r.profile.active_skills:
                all_skills.add(skill.name)

        # 应该有多种不同的技能领域
        assert len(all_skills) >= 3

    def test_g1_focuses_on_direct_relevance(self, diverse_source):
        """测试 G1 关注直接相关性"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        service = WorkerProfileRetrievalService(source=diverse_source)

        result = service.retrieve(
            question="Python web development",
            mode=RetrievalMode.AGENT,
            top_k=3,
        )

        # G1 应该优先匹配 Python 相关的 profile
        assert len(result.results) > 0

        # 第一个结果应该是最相关的（有 Python 技能）
        top_skills = [s.name for s in result.results[0].profile.active_skills]
        assert "Python" in top_skills or "Django" in top_skills


class TestRetrievalServiceIntegration:
    """检索服务集成测试"""

    def test_retrieve_all_modes(self):
        """测试所有模式的检索"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        profiles = [
            create_test_profile(
                staff_id="001",
                profile_id="default",
                skills=["Python", "API"],
                context_kinds=["agent"],
            ),
        ]
        source = MockWorkerProfileSource(profiles)
        service = WorkerProfileRetrievalService(source=source)

        for mode in RetrievalMode:
            result = service.retrieve(
                question="Python programming",
                mode=mode,
                top_k=5,
            )
            assert result.mode == mode

    def test_retrieve_empty_profiles(self):
        """测试空 profiles 检索"""
        from src.domain.services.worker_profile_retrieval_service import (
            WorkerProfileRetrievalService,
        )

        source = MockWorkerProfileSource([])
        service = WorkerProfileRetrievalService(source=source)

        result = service.retrieve(
            question="test question",
            mode=RetrievalMode.AGENT,
            top_k=10,
        )

        assert len(result.results) == 0