import unittest
from pathlib import Path

from clawevolve_plan.spec.builder import build_spec
from clawevolve_plan.spec.contract import validate_spec_markdown
from clawevolve_plan.spec.renderer import render_markdown


class SpecVersionBoundaryTests(unittest.TestCase):
    def test_plan_runtime_continues_to_emit_v0(self):
        plan = {
            "schema_version": "clawevolve.plan-input.v1",
            "bot_id": "bot-1",
            "input_mode": "diagnose",
            "default_optimization_goal": {"goal_text": "improve completion"},
            "root_cause_clusters": [
                {
                    "evolution_failure_mode": "tool_execution_failure",
                    "affected_case_count": 1,
                    "problem_analysis": "tool fallback is uncertain",
                }
            ],
            "cases": [],
        }

        spec = build_spec(
            plan,
            discovery_notes="inspected skills/demo/SKILL.md",
            target_files=["skills/demo/SKILL.md"],
        )
        markdown = render_markdown(spec)

        self.assertEqual(spec["schema_version"], "evolution.spec.v0")
        self.assertEqual(spec["spec_version"], "v0")
        self.assertIn("schema_version: evolution.spec.v0", markdown)
        self.assertIn("## 3. Active Optimization Directions", markdown)
        self.assertNotIn("## Current Experiment Questions", markdown)
        validate_spec_markdown(markdown, spec)

    def test_v1_reference_is_explicitly_documentation_only(self):
        reference = (
            Path(__file__).resolve().parents[1]
            / "references"
            / "spec_schema_v1.md"
        ).read_text(encoding="utf-8")

        self.assertIn("状态：设计草案，当前未启用", reference)
        self.assertIn("Plan 运行时继续生成并校验", reference)
        self.assertIn("evolution.spec.v0", reference)


if __name__ == "__main__":
    unittest.main()
