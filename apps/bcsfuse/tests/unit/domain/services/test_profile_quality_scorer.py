"""
ProfileQualityScorer 单元测试

测试质量评分器的评分逻辑。
"""

import pytest

from src.domain.models.profile_quality_score import (
    QUALITY_THRESHOLD_ACCEPTABLE,
    ProfileQualityScore,
)
from src.domain.models.worker_profile_content import (
    WorkerProfileContent,
    SkillSet,
)
from src.domain.services.profile_quality_scorer import ProfileQualityScorer


class TestProfileQualityScore:
    """ProfileQualityScore 模型测试"""

    def test_is_acceptable_above_threshold(self):
        """分数高于阈值时 is_acceptable 为 True"""
        score = ProfileQualityScore(
            profile_key="test:default",
            total_score=0.5,
            issues=[],
        )
        assert score.is_acceptable is True

    def test_is_acceptable_below_threshold(self):
        """分数低于阈值时 is_acceptable 为 False"""
        score = ProfileQualityScore(
            profile_key="test:default",
            total_score=0.2,
            issues=[],
        )
        assert score.is_acceptable is False

    def test_is_acceptable_at_threshold(self):
        """分数等于阈值时 is_acceptable 为 True"""
        score = ProfileQualityScore(
            profile_key="test:default",
            total_score=QUALITY_THRESHOLD_ACCEPTABLE,
            issues=[],
        )
        assert score.is_acceptable is True


class TestProfileQualityScorer:
    """ProfileQualityScorer 测试"""

    @pytest.fixture
    def scorer(self):
        return ProfileQualityScorer()

    # =========================================================================
    # WorkerProfileContent 评分测试
    # =========================================================================

    def test_score_complete(self, scorer):
        """完整的 WorkerProfileContent 应得高分"""
        content = WorkerProfileContent(
            worker_id="worker001",
            profile_id="default",
            soul_md="x" * 600,
            agents_md="agent config",
            skill_sets=[
                SkillSet(name="skill1", description="desc1"),
                SkillSet(name="skill2", description="desc2"),
                SkillSet(name="skill3", description="desc3"),
            ],
        )

        result = scorer.score_profile_content(content)

        print(f"\n[完整Content] total_score={result.total_score:.4f}, is_acceptable={result.is_acceptable}")
        print(f"  issues: {result.issues}")

        assert result.profile_key == "worker001:default"
        assert result.total_score > 0.6
        assert result.is_acceptable is True
        assert not any("MISSING_SOUL_MD" in i for i in result.issues)

    def test_score_missing_soul(self, scorer):
        """缺少 SOUL.md 应得低分"""
        content = WorkerProfileContent(
            worker_id="worker002",
            profile_id="default",
            soul_md=None,
            skill_sets=[],
        )

        result = scorer.score_profile_content(content)

        print(f"\n[缺SoulContent] total_score={result.total_score:.4f}, is_acceptable={result.is_acceptable}")
        print(f"  issues: {result.issues}")

        assert result.total_score == pytest.approx(0.00)
        assert any("[WARN] 缺少SOUL.md定义" in i for i in result.issues)
        assert any("[WARN] 未配置SKILL" in i for i in result.issues)

    def test_score_short_soul(self, scorer):
        """SOUL.md 太短应有警告"""
        content = WorkerProfileContent(
            worker_id="worker003",
            profile_id="default",
            soul_md="short",  # < 200 字符
            skill_sets=[
                SkillSet(name="skill1", description="desc"),
            ],
        )

        result = scorer.score_profile_content(content)

        print(f"\n[短SoulContent] total_score={result.total_score:.4f}, is_acceptable={result.is_acceptable}")
        print(f"  issues: {result.issues}")

        # SOUL长度>0但<200得0.05 + SKILL数量>=1得0.10 + 文本长度检查
        assert any("SOUL.md内容不够丰富" in i or "缺少SOUL.md定义" in i for i in result.issues)

    def test_score_few_skills(self, scorer):
        """SKILL数量较少的Profile"""
        content = WorkerProfileContent(
            worker_id="worker004",
            profile_id="default",
            soul_md="x" * 200,
            skill_sets=[
                SkillSet(name="skill1", description=""),
                SkillSet(name="skill2", description=None),
            ],
        )

        result = scorer.score_profile_content(content)

        print(f"\n[SKILL数量较少] total_score={result.total_score:.4f}, is_acceptable={result.is_acceptable}")
        print(f"  issues: {result.issues}")

        # SKILL数量>=1(0.10) 而不是检查描述
        assert any("[SUGGEST] SKILL数量较少" in i for i in result.issues)

    # =========================================================================
    # 边界条件测试
    # =========================================================================

    def test_score_minimal_valid(self, scorer):
        """最少的可接受 profile"""
        content = WorkerProfileContent(
            worker_id="worker005",
            profile_id="default",
            soul_md="x" * 250,  # >=200 得0.20
            skill_sets=[
                SkillSet(name="skill1", description="some description"),
                SkillSet(name="skill2", description="desc"),
                SkillSet(name="skill3", description="desc"),  # >=3 得0.20
            ],
        )

        result = scorer.score_profile_content(content)

        print(f"\n[最小有效Content] total_score={result.total_score:.4f}, is_acceptable={result.is_acceptable}")
        print(f"  issues: {result.issues}")

        # SOUL长度>=200(0.20) + SKILL数量>=3(0.20) + 文本长度>=200(0.15) = 0.55
        assert result.total_score >= 0.30

    def test_score_perfect(self, scorer):
        """满分 profile：完整 SOUL + 足够技能"""
        content = WorkerProfileContent(
            worker_id="worker006",
            profile_id="default",
            soul_md="x" * 500,
            agents_md="x" * 200,
            tools_md="x" * 200,
            skill_sets=[
                SkillSet(name=f"skill{i}", description=f"desc{i}")
                for i in range(5)  # 5个技能
            ],
        )

        result = scorer.score_profile_content(content)

        print(f"\n[满分Profile] total_score={result.total_score:.4f}, is_acceptable={result.is_acceptable}")
        print(f"  issues: {result.issues}")

        # 满分: SOUL存在(0.10) + SOUL长度>=500(0.30) + SKILL数量>=5(0.20) + SKILL描述比例(0.10) + 文本长度>=800(0.30) = 1.0
        assert result.total_score == pytest.approx(1.0)
        assert len(result.issues) == 0  # 无警告和建议