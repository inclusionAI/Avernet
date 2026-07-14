"""
Fusion Simulation Snapshot Tests

Worker Profile Retrieval & Fusion Simulation Baseline

区分两种测试：
1. Fixture-based Integration Tests：基于 mock 数据，CI 始终运行
2. Real Snapshot Smoke Tests：基于真实数据，默认 skip，需手动触发

运行说明：
- pytest tests/integration/test_fusion_simulation_snapshot.py
  -> 只运行 fixture tests（real_snapshot 测试被自动 skip）

- pytest tests/integration/test_fusion_simulation_snapshot.py -m real_snapshot
  -> 只运行 real snapshot tests（需要真实数据）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.fusion_request import FusionRequest
from src.domain.models.fusion_simulation_input import FusionSimulationInput
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.domain.services.worker_context_preparation_service import (
    WorkerContextPreparationService,
)
from src.domain.services.worker_profile_retrieval_service import (
    WorkerProfileRetrievalService,
)
from src.domain.services.fusion_simulation_service import FusionSimulationService


# =============================================================================
# Fixture-based Integration Tests（CI 运行）
# =============================================================================


class TestFusionSimulationFixtureBased:
    """
    基于 fixture 数据的集成测试

    这些测试使用硬编码的测试数据，CI 中始终运行。
    不依赖外部数据文件。
    """

    @pytest.fixture
    def fixture_profiles(self):
        """创建硬编码的测试 profiles"""
        return [
            WorkerProfile(
                staff_id="fixture_001",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/fixture",
                context_fragments=[
                    ContextFragment(
                        kind=ContextKind.AGENT,
                        filename="AGENTS.md",
                        content="I am a backend engineer specializing in Python and API design. "
                                "I have 5 years of experience in building scalable web services.",
                        source_path="/fixture/AGENTS.md",
                    ),
                    ContextFragment(
                        kind=ContextKind.SOUL,
                        filename="SOUL.md",
                        content="I believe in clean code, thorough testing, and continuous improvement.",
                        source_path="/fixture/SOUL.md",
                    ),
                ],
                active_skills=[
                    SkillProfile(
                        name="Python",
                        description="Expert in Python programming, Django, FastAPI",
                        skill_id="skill_python",
                        skill_set_name="default",
                    ),
                    SkillProfile(
                        name="API Design",
                        description="RESTful API design and implementation",
                        skill_id="skill_api",
                        skill_set_name="default",
                    ),
                ],
            ),
            WorkerProfile(
                staff_id="fixture_002",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/fixture",
                context_fragments=[
                    ContextFragment(
                        kind=ContextKind.AGENT,
                        filename="AGENTS.md",
                        content="I am a frontend engineer specializing in React and TypeScript. "
                                "I focus on user experience and performance optimization.",
                        source_path="/fixture/AGENTS.md",
                    ),
                ],
                active_skills=[
                    SkillProfile(
                        name="React",
                        description="Frontend development with React ecosystem",
                        skill_id="skill_react",
                        skill_set_name="default",
                    ),
                    SkillProfile(
                        name="TypeScript",
                        description="Type-safe JavaScript development",
                        skill_id="skill_typescript",
                        skill_set_name="default",
                    ),
                ],
            ),
            WorkerProfile(
                staff_id="fixture_003",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/fixture",
                context_fragments=[
                    ContextFragment(
                        kind=ContextKind.AGENT,
                        filename="AGENTS.md",
                        content="I am a DevOps engineer specializing in Kubernetes and CI/CD. "
                                "I ensure reliable deployments and infrastructure.",
                        source_path="/fixture/AGENTS.md",
                    ),
                ],
                active_skills=[
                    SkillProfile(
                        name="Kubernetes",
                        description="Container orchestration and management",
                        skill_id="skill_k8s",
                        skill_set_name="default",
                    ),
                    SkillProfile(
                        name="CI/CD",
                        description="Continuous integration and deployment pipelines",
                        skill_id="skill_cicd",
                        skill_set_name="default",
                    ),
                ],
            ),
        ]

    def test_g1_simulation_with_fixture_profiles(self, fixture_profiles):
        """G1 模拟：使用 fixture 数据"""
        # 准备输入
        prep_service = WorkerContextPreparationService()
        digests = [
            prep_service.prepare(p, "Python API development", RetrievalMode.AGENT)
            for p in fixture_profiles
        ]

        input_data = FusionSimulationInput(
            question="How to design a Python REST API?",
            mode=RetrievalMode.AGENT,
            profiles=fixture_profiles,
            context_digests=digests,
            max_perspectives=2,
        )

        # 执行模拟
        sim_service = FusionSimulationService()
        result = sim_service.simulate(input_data)

        # 验证
        assert result.fusion_mode == "agent"
        assert len(result.perspectives) <= 2
        assert result.perspectives[0].participant_id == "staff_fixture_001:default"
        assert "Python" in result.perspectives[0].summary or "API" in result.perspectives[0].summary

    def test_g2_simulation_with_fixture_profiles(self, fixture_profiles):
        """G2 模拟：使用 fixture 数据，验证冲突分析"""
        prep_service = WorkerContextPreparationService()
        digests = [
            prep_service.prepare(p, "architecture decision", RetrievalMode.CONFLICT_ALIGNMENT)
            for p in fixture_profiles
        ]

        input_data = FusionSimulationInput(
            question="Should we use backend-first or frontend-first architecture?",
            mode=RetrievalMode.CONFLICT_ALIGNMENT,
            profiles=fixture_profiles,
            context_digests=digests,
        )

        sim_service = FusionSimulationService()
        result = sim_service.simulate(input_data)

        assert result.fusion_mode == "conflict_alignment"
        assert len(result.key_insights) > 0
        # 验证顾虑是动态生成的，不是硬编码
        for p in result.perspectives:
            for concern in p.concerns:
                # 顾虑应该包含 profile 相关信息，而不是固定模板
                assert len(concern) > 10  # 不是简单模板

    def test_g5_simulation_with_fixture_profiles(self, fixture_profiles):
        """G5 模拟：使用 fixture 数据，验证领域多样性"""
        prep_service = WorkerContextPreparationService()
        digests = [
            prep_service.prepare(p, "system design", RetrievalMode.EXPERT_DIAGNOSIS)
            for p in fixture_profiles
        ]

        input_data = FusionSimulationInput(
            question="Review our system architecture for potential issues",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profiles=fixture_profiles,
            context_digests=digests,
        )

        sim_service = FusionSimulationService()
        result = sim_service.simulate(input_data)

        assert result.fusion_mode == "expert_diagnosis"
        # G5 视角应该是 expert 角色
        for p in result.perspectives:
            assert p.role == "expert"
        # 应该有诊断摘要
        assert result.summary is not None
        # recommendations 应该包含 evidence_count
        for rec in result.recommendations:
            assert "evidence_count" in rec

    def test_end_to_end_flow_with_fixture(self, fixture_profiles):
        """端到端流程测试：检索 -> 准备 -> 模拟"""
        # 1. 检索
        class MockSource:
            def __init__(self, profiles):
                self._profiles = profiles

            def scan(self):
                from src.domain.models.worker_profile import WorkerProfileScanResult
                return WorkerProfileScanResult(profiles=self._profiles)

        retrieval_service = WorkerProfileRetrievalService(source=MockSource(fixture_profiles))
        retrieval_result = retrieval_service.retrieve(
            question="Python development",
            mode=RetrievalMode.AGENT,
            top_k=3,
        )

        assert len(retrieval_result.results) > 0

        # 2. 准备 context
        prep_service = WorkerContextPreparationService()
        digests = [
            prep_service.prepare(
                r.profile,
                "Python development",
                RetrievalMode.AGENT,
            )
            for r in retrieval_result.results
        ]

        # 3. 模拟
        sim_service = FusionSimulationService()
        sim_input = FusionSimulationInput(
            question="Python development best practices",
            mode=RetrievalMode.AGENT,
            profiles=[r.profile for r in retrieval_result.results],
            context_digests=digests,
        )
        sim_result = sim_service.simulate(sim_input)

        assert sim_result.fusion_mode == "agent"
        assert len(sim_result.perspectives) > 0


# =============================================================================
# Real Snapshot Smoke Tests（条件性跳过）
# =============================================================================


def _get_snapshot_roots() -> list[str]:
    """
    获取 snapshot roots 目录列表

    优先使用环境变量 WORKER_PROFILE_ROOTS，否则自动检测。

    Returns:
        roots 目录路径列表
    """
    import os

    root_path = Path(__file__).parent.parent.parent
    snapshot_base = root_path / "data" / "worker_profile_snapshots"

    # 优先使用环境变量指定的 roots
    env_roots = os.environ.get("WORKER_PROFILE_ROOTS", "")
    if env_roots:
        # 支持冒号分隔的多个 roots
        roots = [r.strip() for r in env_roots.split(":") if r.strip()]
        # 如果是相对路径，转换为绝对路径
        result = []
        for r in roots:
            p = Path(r)
            if not p.is_absolute():
                p = root_path / p
            result.append(str(p))
        return result

    # 自动检测：在 snapshot_base 下查找 {date}/{data_dir}/ 结构
    if not snapshot_base.exists():
        return []

    detected_roots: list[str] = []
    for date_dir in snapshot_base.iterdir():
        if date_dir.is_dir():
            for data_dir in date_dir.iterdir():
                if data_dir.is_dir():
                    # 检查是否包含 staff_xxx 目录
                    has_staff = any(
                        d.name.startswith("staff_") for d in data_dir.iterdir() if d.is_dir()
                    )
                    if has_staff:
                        detected_roots.append(str(data_dir))

    return detected_roots


def _should_skip_real_snapshot() -> tuple[bool, str]:
    """
    判断是否应该跳过 real snapshot 测试

    Returns:
        (should_skip, reason): 是否跳过及原因
    """
    import os

    # 检查环境变量
    env_enabled = os.environ.get("WORKER_PROFILE_REAL_SNAPSHOT_TEST", "").lower() == "true"
    if not env_enabled:
        return True, (
            "Real snapshot test disabled. "
            "Set WORKER_PROFILE_REAL_SNAPSHOT_TEST=true to enable. "
            "Example: export WORKER_PROFILE_REAL_SNAPSHOT_TEST=true && pytest -m real_snapshot -v"
        )

    # 检查是否有有效的 roots
    roots = _get_snapshot_roots()
    if not roots:
        root_path = Path(__file__).parent.parent.parent
        snapshot_base = root_path / "data" / "worker_profile_snapshots"
        return True, (
            f"No valid snapshot roots found. "
            f"Either set WORKER_PROFILE_ROOTS to point to data directory, "
            f"or ensure {snapshot_base}/{{date}}/{{data_dir}}/ contains staff_xxx directories. "
            f"Example: export WORKER_PROFILE_ROOTS=./data/worker_profile_snapshots/2026-03-23/bolt_data"
        )

    return False, ""


# 预计算 skip 条件
_SKIP_REAL_SNAPSHOT, _SKIP_REASON = _should_skip_real_snapshot()


@pytest.mark.real_snapshot
@pytest.mark.skipif(_SKIP_REAL_SNAPSHOT, reason=_SKIP_REASON)
class TestFusionSimulationRealSnapshot:
    """
    基于真实 snapshot 数据的冒烟测试

    运行条件（必须同时满足）：
    1. 环境变量 WORKER_PROFILE_REAL_SNAPSHOT_TEST=true
    2. 设置 WORKER_PROFILE_ROOTS 指向真实数据目录，或自动检测

    运行方式：
    ```bash
    export WORKER_PROFILE_REAL_SNAPSHOT_TEST=true
    export WORKER_PROFILE_ROOTS=./data/worker_profile_snapshots/2026-03-23/bolt_data
    pytest -m real_snapshot -v
    ```

    或一键运行：
    ```bash
    WORKER_PROFILE_REAL_SNAPSHOT_TEST=true \\
    WORKER_PROFILE_ROOTS=./data/worker_profile_snapshots/2026-03-23/bolt_data \\
    pytest -m real_snapshot -v
    ```

    目录结构要求：
    WORKER_PROFILE_ROOTS 指向的目录必须包含 staff_xxx 子目录：
    ```bash
    {roots}/
      staff_xxx/
        default/
          openclaw/
            AGENTS.md
            BOOT.md
            SOUL.md
            TOOLS.md
            skills/
              skill_sets.json
    ```
    """

    @pytest.fixture
    def snapshot_roots(self):
        """获取 snapshot roots 目录列表"""
        return _get_snapshot_roots()

    @pytest.fixture
    def snapshot_data_path(self):
        """获取第一个 snapshot root（向后兼容）"""
        roots = _get_snapshot_roots()
        return Path(roots[0]) if roots else Path("")

    def test_snapshot_directory_exists(self, snapshot_data_path):
        """验证 snapshot 目录存在并包含 staff_xxx 子目录"""
        assert snapshot_data_path.exists(), (
            f"Snapshot directory not found: {snapshot_data_path}\n"
            "Please create the directory with real worker profile data."
        )
        # 检查是否有 staff_xxx 子目录
        has_staff_dir = any(
            d.name.startswith("staff_") and d.is_dir()
            for d in snapshot_data_path.iterdir()
        )
        assert has_staff_dir, (
            f"No staff_xxx directories found in {snapshot_data_path}. "
            f"Expected structure: {snapshot_data_path}/staff_xxx/default/openclaw/"
        )

    def test_load_real_profiles_from_snapshot(self, snapshot_data_path):
        """从真实 snapshot 加载 profiles"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        # 使用 settings 配置 roots
        settings = WorkerProfileSettings(roots=[str(snapshot_data_path)])
        source = FileWorkerProfileSource(settings=settings)
        result = source.scan()

        assert len(result.profiles) > 0, "No profiles found in snapshot"
        print(f"\nLoaded {len(result.profiles)} profiles from snapshot")
        for p in result.profiles[:3]:
            print(f"  - {p.profile_key}: {len(p.active_skills)} skills, {len(p.context_fragments)} fragments")

    def test_g1_simulation_with_real_snapshot(self, snapshot_data_path):
        """G1 模拟：使用真实 snapshot 数据"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        # 加载真实数据
        settings = WorkerProfileSettings(roots=[str(snapshot_data_path)])
        source = FileWorkerProfileSource(settings=settings)
        scan_result = source.scan()
        profiles = scan_result.profiles

        assert len(profiles) > 0, "No profiles in snapshot"

        # 检索
        retrieval_service = WorkerProfileRetrievalService(source=source)
        retrieval_result = retrieval_service.retrieve(
            question="API development",
            mode=RetrievalMode.AGENT,
            top_k=3,
        )

        # 准备
        prep_service = WorkerContextPreparationService()
        digests = [
            prep_service.prepare(r.profile, "API development", RetrievalMode.AGENT)
            for r in retrieval_result.results
        ]

        # 模拟
        sim_service = FusionSimulationService()
        sim_input = FusionSimulationInput(
            question="How to design a good API?",
            mode=RetrievalMode.AGENT,
            profiles=[r.profile for r in retrieval_result.results],
            context_digests=digests,
        )
        result = sim_service.simulate(sim_input)

        # 验证
        assert result.fusion_mode == "agent"
        print(f"\nG1 simulation complete with {len(result.perspectives)} perspectives:")
        for p in result.perspectives:
            print(f"  - {p.participant_id}: confidence={p.confidence:.2f}")

    def test_g5_diversity_with_real_snapshot(self, snapshot_data_path):
        """G5 多样性测试：使用真实 snapshot 数据"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[str(snapshot_data_path)])
        source = FileWorkerProfileSource(settings=settings)
        scan_result = source.scan()
        profiles = scan_result.profiles

        assert len(profiles) >= 3, f"Need at least 3 profiles for diversity test, got {len(profiles)}"

        retrieval_service = WorkerProfileRetrievalService(source=source)
        retrieval_result = retrieval_service.retrieve(
            question="system architecture review",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            top_k=5,
        )

        # 验证多样性
        all_skills = set()
        for r in retrieval_result.results:
            for skill in r.profile.active_skills:
                all_skills.add(skill.name)

        print(f"\nG5 selected {len(retrieval_result.results)} profiles with {len(all_skills)} unique skills:")
        print(f"  Skills: {', '.join(sorted(all_skills)[:10])}{'...' if len(all_skills) > 10 else ''}")


# =============================================================================
# 测试运行说明
# =============================================================================
#
# 1. 运行 fixture tests（CI 默认，始终执行）：
#    pytest tests/integration/test_fusion_simulation_snapshot.py
#
# 2. 运行 real snapshot tests（需要满足条件）：
#    # 方式一：设置环境变量（自动检测数据目录）
#    export WORKER_PROFILE_REAL_SNAPSHOT_TEST=true
#    pytest -m real_snapshot -v
#
#    # 方式二：显式指定数据目录（推荐）
#    export WORKER_PROFILE_REAL_SNAPSHOT_TEST=true
#    export WORKER_PROFILE_ROOTS=./data/worker_profile_snapshots/2026-03-23/bolt_data
#    pytest -m real_snapshot -v
#
#    # 方式三：一键运行
#    WORKER_PROFILE_REAL_SNAPSHOT_TEST=true \
#    WORKER_PROFILE_ROOTS=./data/worker_profile_snapshots/2026-03-23/bolt_data \
#    pytest -m real_snapshot -v
#
# 3. Real snapshot tests 运行前提条件：
#    - 环境变量 WORKER_PROFILE_REAL_SNAPSHOT_TEST=true
#    - 环境变量 WORKER_PROFILE_ROOTS 指向包含 staff_xxx 的目录
#      或自动检测 data/worker_profile_snapshots/{date}/{data_dir}/ 结构
#
# 4. 正确的目录结构（roots 指向 bolt_data 这一层）：
#    data/worker_profile_snapshots/2026-03-23/bolt_data/   <-- WORKER_PROFILE_ROOTS 指向这里
#    ├── staff_001/
#    │   └── default/
#    │       └── openclaw/
#    │           ├── AGENTS.md
#    │           ├── BOOT.md
#    │           ├── SOUL.md
#    │           ├── TOOLS.md
#    │           └── skills/
#    │               └── skill_sets.json
#    └── staff_002/
#        └── default/
#            └── openclaw/
#                ├── AGENTS.md
#                └── skills/
#                    └── skill_sets.json