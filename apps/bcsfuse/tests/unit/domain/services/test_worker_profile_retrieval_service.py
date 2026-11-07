"""
Tests for Worker Profile Retrieval Service

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：Fuse DTO 和 TC 当前资料检索契约。
"""

from __future__ import annotations

from typing import Optional

from app.domain.models.context_fragment import ContextFragment, ContextKind
from app.domain.models.retrieval_mode import RetrievalMode
from app.domain.models.scoring_signal import ScoringSignal, SignalType
from app.domain.models.skill_profile import SkillProfile
from app.domain.models.worker_profile import ProfileType, WorkerProfile


class MockWorkerProfileSource:
    """Mock WorkerProfileSource for testing"""

    def __init__(self, profiles: list[WorkerProfile]):
        self._profiles = profiles

    def scan(self):
        """Scan all profiles"""
        from app.domain.models.worker_profile import WorkerProfileScanResult
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
        from app.domain.services.worker_profile_retrieval_service import RetrievalResult

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
        from app.domain.services.worker_profile_retrieval_service import RetrievalResult

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




from app.adapters.http.dependencies import fusion_dependencies as dependencies
from tests.contract.test_tc_catalog import runtime as tc_runtime  # noqa: F401
from tests.contract.test_tc_catalog import sync


def test_strict_missing_participant_cannot_fall_back_to_another_worker(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    response = dependencies.get_profile_retrieval_service().retrieve(
        "MySQL", RetrievalMode.EXPERT_DIAGNOSIS, profile_keys=["missing:legacy"],
        strict_participants=True,
    )
    assert response.results == []


def test_non_strict_missing_participant_can_use_shared_retrieval(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    response = dependencies.get_profile_retrieval_service().retrieve(
        "MySQL", RetrievalMode.EXPERT_DIAGNOSIS, profile_keys=["missing:legacy"],
        strict_participants=False,
    )
    assert [result.profile.staff_id for result in response.results] == ["bot:owner"]


def test_deleted_participant_is_not_recovered_from_stale_profiles(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    client.delete("/v1/workers/bot:owner").raise_for_status()
    response = dependencies.get_profile_retrieval_service().retrieve(
        "MySQL", RetrievalMode.EXPERT_DIAGNOSIS, profile_keys=["bot:owner:legacy"],
        strict_participants=True,
    )
    assert response.results == []
