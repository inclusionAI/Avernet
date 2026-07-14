"""
Profiling Results Fixtures

M2: Worker Profiling & Extraction

提供预期的 profiling 结果样例，供集成测试断言使用。

原则：
- 与 profiling_documents.py 样例对应
- 只定义关键断言条件，不硬编码完整结果
"""

from __future__ import annotations

from typing import Any

from src.domain.models.profiling_input import DocType
from src.domain.models.profiling_result import (
    CapabilityLevel,
    ConstraintPolicy,
    SkillSource,
    TrustLevel,
    ResourceKind,
    ResourceAccess,
)


# =============================================================================
# SOUL 完整文档的预期结果
# =============================================================================

SOUL_COMPLETE_EXPECTED = {
    "capabilities": [
        {"name": "Information Retrieval", "level": CapabilityLevel.EXPERT},
        {"name": "Data Analysis", "level": CapabilityLevel.ADVANCED},
        {"name": "Report Generation", "level": CapabilityLevel.INTERMEDIATE},
    ],
    "domains": [
        "architecture",
        "documentation",
        "research",
    ],
    "responsibilities_min_count": 3,
    "collaboration_style": {
        "preference": "async_communication",
    },
    "skills_min_count": 3,  # 3 from SOUL + metadata
    "resources_min_count": 2,  # 2 from SOUL + metadata
}


# =============================================================================
# RULES 完整文档的预期结果
# =============================================================================

RULES_COMPLETE_EXPECTED = {
    "constraints": {
        "forbidden_min_count": 2,
        "approval_required_min_count": 1,
    },
    "escalation_triggers_min_count": 2,
}


# =============================================================================
# MEMORY 完整文档的预期结果
# =============================================================================

MEMORY_COMPLETE_EXPECTED = {
    "episodes_min_count": 3,
    "timestamps": ["2026-03-01", "2026-03-15", "2026-03-20"],
}


# =============================================================================
# 完整输入的预期结果
# =============================================================================

def assert_complete_result(result: Any) -> list[str]:
    """
    断言完整输入的预期结果

    Args:
        result: WorkerProfileExtractionResult

    Returns:
        检查通过的断言列表
    """
    checks: list[str] = []

    # 检查 worker_id
    assert result.worker_id == "wrk_complete_001", "worker_id should match"
    checks.append("worker_id matches")

    # 检查 capabilities
    assert len(result.capabilities) >= 3, "Should have at least 3 capabilities"
    checks.append("capabilities count OK")

    cap_names = [c.name for c in result.capabilities]
    assert "Information Retrieval" in cap_names, "Should have Information Retrieval capability"
    checks.append("has Information Retrieval capability")

    # 检查 domains
    assert len(result.domains) >= 3, "Should have at least 3 domains"
    checks.append("domains count OK")

    domain_names = [d.name for d in result.domains]
    assert "architecture" in domain_names, "Should have architecture domain"
    checks.append("has architecture domain")

    # 检查 constraints
    assert len(result.constraints) >= 2, "Should have at least 2 constraints"
    checks.append("constraints count OK")

    # 检查 escalation triggers
    assert len(result.escalation_triggers) >= 2, "Should have at least 2 escalation triggers"
    checks.append("escalation_triggers count OK")

    # 检查 skills (from SOUL + metadata)
    assert len(result.skills) >= 2, "Should have at least 2 skills"
    checks.append("skills count OK")

    skill_names = [s.name for s in result.skills]
    assert "web_search" in skill_names, "Should have web_search skill"
    checks.append("has web_search skill")

    # 检查 resources
    assert len(result.resources) >= 2, "Should have at least 2 resources"
    checks.append("resources count OK")

    # 检查 memory episodes
    assert len(result.memory_episodes) >= 3, "Should have at least 3 memory episodes"
    checks.append("memory_episodes count OK")

    # 检查 collaboration_style
    assert result.collaboration_style is not None, "Should have collaboration_style"
    checks.append("has collaboration_style")

    # 检查 complete (no errors)
    assert result.is_complete(), "Result should be complete (no errors)"
    checks.append("result is complete")

    return checks


def assert_source_references_valid(result: Any) -> list[str]:
    """
    断言 source references 有效

    Args:
        result: WorkerProfileExtractionResult

    Returns:
        检查通过的断言列表
    """
    checks: list[str] = []

    # 检查 capabilities 的 source_ref
    if result.capabilities:
        cap = result.capabilities[0]
        assert cap.source_ref is not None, "Capability should have source_ref"
        assert cap.source_ref.doc_type == DocType.SOUL, "Capability source should be SOUL"
        checks.append("capability source_ref is valid")

    # 检查 constraints 的 source_ref
    if result.constraints:
        constraint = result.constraints[0]
        assert constraint.source_ref is not None, "Constraint should have source_ref"
        assert constraint.source_ref.doc_type == DocType.RULES, "Constraint source should be RULES"
        checks.append("constraint source_ref is valid")

    # 检查 memory episodes 的 source_ref
    if result.memory_episodes:
        episode = result.memory_episodes[0]
        assert episode.source_ref is not None, "Memory episode should have source_ref"
        assert episode.source_ref.doc_type == DocType.MEMORY, "Memory episode source should be MEMORY"
        checks.append("memory_episode source_ref is valid")

    return checks


def assert_deduplication_worked(result: Any) -> list[str]:
    """
    断言去重生效

    Args:
        result: WorkerProfileExtractionResult

    Returns:
        检查通过的断言列表
    """
    checks: list[str] = []

    # 检查 skills 不重复
    skill_names = [s.name.lower() for s in result.skills]
    assert len(skill_names) == len(set(skill_names)), "Skills should be deduplicated"
    checks.append("skills deduplicated")

    # 检查 resources 不重复
    resource_ids = [r.id for r in result.resources]
    assert len(resource_ids) == len(set(resource_ids)), "Resources should be deduplicated"
    checks.append("resources deduplicated")

    return checks


__all__ = [
    "SOUL_COMPLETE_EXPECTED",
    "RULES_COMPLETE_EXPECTED",
    "MEMORY_COMPLETE_EXPECTED",
    "assert_complete_result",
    "assert_source_references_valid",
    "assert_deduplication_worked",
]