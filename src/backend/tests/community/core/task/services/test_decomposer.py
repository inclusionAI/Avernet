"""TDD for the rule-based DecomposerService (Phase 4.3, plan §5.2)."""
from __future__ import annotations

from agentclaw.community.core.task.services.decomposer_service import DecomposerService


def test_decompose_splits_clauses_into_subtasks():
    plan = DecomposerService().decompose_spec("research the topic; draft the report; publish it")
    assert len(plan.sub_tasks) == 3
    assert plan.sub_tasks[0].spec == "research the topic"
    assert all(s.node_id.startswith("n") for s in plan.sub_tasks)


def test_decompose_dedups_near_identical_clauses():
    plan = DecomposerService().decompose_spec("research the topic; research the topicx")
    # high similarity → deduped to one
    assert len(plan.sub_tasks) == 1


def test_decompose_empty_spec_yields_empty_plan():
    plan = DecomposerService().decompose_spec("")
    assert plan.sub_tasks == []


def test_decompose_broad_spec_yields_low_confidence打回():
    plan = DecomposerService().decompose_spec("a; b; c; d; e; f; g")
    assert plan.confidence < 0.7


def test_decompose_tight_spec_yields_high_confidence():
    plan = DecomposerService().decompose_spec("do one thing")
    assert plan.confidence >= 0.7