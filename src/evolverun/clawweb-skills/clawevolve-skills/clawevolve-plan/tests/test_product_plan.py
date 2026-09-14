from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clawevolve_plan.product import (  # noqa: E402
    GoalConstraintSet,
    GoalFidelityValidator,
    PlanProductService,
)
from clawevolve_plan.spec.builder import build_objective_document, build_spec  # noqa: E402
from clawevolve_plan.spec.renderer import render_goal_markdown, render_markdown  # noqa: E402


GOAL = (
    "创建一个能统计bot每天开启了多少新session的skill，创建5个bench templates用于验证skill的正确性，"
    "并且最终任务成功率要达到100%"
)


def direct_plan() -> dict:
    cases = [
        {
            "case_id": f"goal-case-{index:03d}",
            "case_type": "prospective",
            "query": f"统计第 {index} 天的新 session 数量",
            "evolution_failure_mode": "missing_skill_capability",
        }
        for index in range(1, 6)
    ]
    return {
        "schema_version": "clawevolve.plan-input.v1",
        "input_mode": "direct_goal",
        "diagnose_source": "direct_goal",
        "user_intent": {
            "raw_request": GOAL,
            "intent_text": GOAL,
            "confidence": 1.0,
        },
        "case_preference": {"raw_message": GOAL, "intent_text": GOAL},
        "goal_context": {
            "raw_goal": GOAL,
            "required_capabilities": ["按自然日统计 bot 新 session 数量"],
            "quality_requirements": ["统计结果必须可核验"],
            "constraints": ["不得编造 session 数量"],
            "requested_deliverables": [
                "创建 session 统计 Skill",
                "创建 5 个 bench templates",
            ],
        },
        "default_optimization_goal": {
            "goal_text": GOAL,
            "target_task_success_rate": 1.0,
        },
        "cases": cases,
        "root_cause_clusters": [
            {
                "evolution_failure_mode": "missing_skill_capability",
                "affected_case_count": 5,
                "problem_analysis": "当前缺少按日统计 session 的 Skill。",
                "optimization_goal": "补齐可核验的按日统计能力。",
                "example_case_ids": [item["case_id"] for item in cases],
            }
        ],
        "creation_scopes": ["skills/skills-local"],
        "reference_files": ["skills/skills-local/example/SKILL.md"],
        "planned_deliverables": [
            {
                "path": "skills/skills-local/session-stats/SKILL.md",
                "operation": "create",
                "creation_scope": "skills/skills-local",
                "deliverable_type": "skill",
            }
        ],
        "artifacts": {},
    }


def build_direct_spec() -> dict:
    return build_spec(
        direct_plan(),
        goal_override=GOAL,
        discovery_notes="direct_goal；已检查创建范围与参考 Skill。",
        target_files=["skills/skills-local"],
    )


class PlanProductViewTests(unittest.TestCase):
    def test_direct_goal_basis_is_not_described_as_historical_diagnosis(self) -> None:
        spec = build_direct_spec()
        basis = spec["planning_basis"]
        self.assertEqual(basis["mode"], "direct_goal")
        self.assertEqual(basis["mode_label"], "用户直接需求")
        self.assertEqual(basis["evidence_case_count"], 5)
        self.assertEqual(basis["creation_scopes"], ["skills/skills-local"])

    def test_diagnose_and_plan_source_basis_are_distinguishable(self) -> None:
        for mode, label in (
            ("diagnose", "Diagnose 诊断结果"),
            ("plan_source", "冻结的 Plan Source"),
        ):
            with self.subTest(mode=mode):
                plan = direct_plan()
                plan["input_mode"] = mode
                plan["diagnose_source"] = (
                    "insight_improvement" if mode == "plan_source" else "local"
                )
                plan["default_optimization_goal"] = {"goal_text": "修复工具调用失败"}
                plan["user_intent"] = {
                    "raw_request": "修复工具调用失败",
                    "intent_text": "修复工具调用失败",
                }
                plan["goal_context"] = {}
                plan["cases"] = plan["cases"][:2]
                spec = build_spec(
                    plan,
                    discovery_notes="已检查 tool failure 目标。",
                    target_files=["skills/example/SKILL.md"],
                )
                self.assertEqual(spec["planning_basis"]["mode_label"], label)
                self.assertEqual(
                    spec["planning_basis"]["diagnosed_problem_modes"],
                    ["missing_skill_capability"],
                )

    def test_plan_summary_contains_goal_capability_direction_boundary_and_acceptance(
        self,
    ) -> None:
        summary = build_direct_spec()["plan_summary"]
        self.assertEqual(summary["goal"], GOAL)
        self.assertIn(
            "按自然日统计 bot 新 session 数量", summary["expected_capabilities"]
        )
        self.assertTrue(summary["optimization_directions"])
        self.assertTrue(summary["change_boundary"]["plan_is_read_only"])
        self.assertEqual(
            summary["change_boundary"]["planned_deliverables"][0]["path"],
            "skills/skills-local/session-stats/SKILL.md",
        )
        self.assertEqual(summary["acceptance_criteria"]["primary_metric"]["target"], 1.0)

    def test_markdown_exposes_basis_and_read_only_product_boundary(self) -> None:
        spec = build_direct_spec()
        objective = build_objective_document(spec)
        spec_md = render_markdown(spec)
        objective_md = render_goal_markdown(objective)
        self.assertIn("规划依据：用户直接需求", spec_md)
        self.assertIn("Plan 只输出后续进化目标与边界", spec_md)
        self.assertIn("规划依据：用户直接需求", objective_md)
        self.assertNotIn("历史 session 事实", spec["planning_basis"]["mode_label"])


class GoalFidelityTests(unittest.TestCase):
    def test_numeric_constraints_are_preserved_exactly(self) -> None:
        spec = build_direct_spec()
        report = spec["goal_fidelity"]
        constraints = {item["key"]: item for item in report["constraints"]}
        self.assertEqual(constraints["bench_template_count"]["actual"], 5)
        self.assertEqual(constraints["primary_metric_target"]["actual"], 1.0)
        self.assertEqual(constraints["bench_template_count"]["status"], "preserved")
        self.assertEqual(constraints["primary_metric_target"]["status"], "preserved")

    def test_numeric_constraint_loss_blocks_document_generation(self) -> None:
        plan = direct_plan()
        spec = build_direct_spec()
        spec["evidence"]["case_count"] = 4
        constraints = GoalConstraintSet.from_plan(plan, goal_override=GOAL)
        with self.assertRaisesRegex(ValueError, "bench_template_count"):
            GoalFidelityValidator().validate(spec, constraints)

    def test_semantic_constraint_is_preserved_without_brittle_failure(self) -> None:
        plan = direct_plan()
        spec = build_direct_spec()
        spec["plan_summary"]["expected_capabilities"] = []
        report = GoalFidelityValidator().validate(
            spec, GoalConstraintSet.from_plan(plan, goal_override=GOAL)
        )
        self.assertIn(report.status, {"passed", "passed_with_warnings"})
        self.assertTrue(
            any(
                item["category"] == "requested_deliverable"
                and item["expected"] == "创建 session 统计 Skill"
                for item in report.constraints
            )
        )

    def test_product_service_refreshes_archived_source_path(self) -> None:
        plan = direct_plan()
        spec = build_direct_spec()
        PlanProductService().enrich(
            plan=plan,
            spec=spec,
            goal_override=GOAL,
            source_path="/run/plan/input/source.json",
        )
        self.assertEqual(
            spec["planning_basis"]["source_path"],
            "/run/plan/input/source.json",
        )


if __name__ == "__main__":
    unittest.main()
