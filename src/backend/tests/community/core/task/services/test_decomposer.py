"""TDD for the rule-based DecomposerService (Phase 4.3, plan §5.2).

单签名 ``decompose_subtasks(spec, state) -> list[SubTaskSpec]``(2026-08-03:旧
``decompose_spec``/``decompose`` 随 ``Plan`` 退场已删)。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskState
from agentclaw.community.core.task.services.decomposer_service import DecomposerService


def _subtasks(spec: str, parent_depth: int = -1) -> list:
    state = TaskState()
    if parent_depth >= 0:
        state.public["__decompose_parent_depth__"] = parent_depth
    return DecomposerService().decompose_subtasks(spec, state)


def test_decompose_splits_clauses_into_subtasks():
    subs = _subtasks("research the topic; draft the report; publish it")
    assert len(subs) == 3
    assert subs[0].spec == "research the topic"
    assert all(s.node_id.startswith("n") for s in subs)


def test_decompose_dedups_near_identical_clauses():
    subs = _subtasks("research the topic; research the topicx")
    # high similarity → deduped to one
    assert len(subs) == 1


def test_decompose_empty_spec_yields_no_children():
    assert _subtasks("") == []


def test_decompose_top_level_children_depth_zero():
    subs = _subtasks("a; b")  # 无父深度 → 顶层 children depth=0
    assert all(s.depth == 0 for s in subs)


def test_decompose_children_depth_is_parent_plus_one():
    subs = _subtasks("a; b", parent_depth=2)  # 父 depth=2 → children depth=3
    assert all(s.depth == 3 for s in subs)