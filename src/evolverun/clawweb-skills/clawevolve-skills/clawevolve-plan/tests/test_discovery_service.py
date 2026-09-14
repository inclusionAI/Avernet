from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.discovery.service import run_auto_discovery  # noqa: E402


TARGET = "skills/example/SKILL.md"


def make_workspace(root: Path) -> Path:
    workspace = root / ".openclaw" / "workspace"
    target = workspace / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("# Example\n", encoding="utf-8")
    return workspace


def discovery_payload(workspace: Path, *, target: str = TARGET) -> dict:
    return {
        "schema_version": "clawevolve.plan.discovery.v1",
        "workspace_root": str(workspace),
        "analysis_summary": {
            "diagnosed_problem": "工具调用失败",
            "environment_root_cause": "目标 Skill 缺少失败恢复规则",
            "optimization_strategy": "在目标 Skill 中补充安全恢复规则",
        },
        "case_findings": [
            {
                "case_id": "case-001",
                "case_type": "bad",
                "failure_mode": "tool_execution_failure",
                "symptom": "工具失败后未恢复",
                "evidence": [target],
                "inspected_files": [target],
                "environment_analysis": f"已检查 {target}，缺少失败恢复规则",
                "optimization_ideas": ["补充有限重试和安全降级"],
                "root_cause_hypothesis": "Skill 契约不完整",
                "confidence": "high",
            }
        ],
        "target_findings": [
            {
                "path": target,
                "target_type": "skill",
                "reason": "最窄且实际检查过的修改入口",
                "current_gap": "缺少失败恢复规则",
                "proposed_change": "补充有限重试和安全降级",
                "related_case_ids": ["case-001"],
                "failure_modes": ["tool_execution_failure"],
                "confidence": "high",
            }
        ],
        "merged_targets": [target],
        "forbidden_boundary_check": {
            "passed": True,
            "checked": ["targets are inside workspace_root and narrow"],
            "notes": "仅选择已检查的目标 Skill",
        },
        "warnings": [],
    }


def agent_result(*, response_text: str = "", status: str = "success"):
    return SimpleNamespace(
        status=status,
        agent_id="test-discovery-agent",
        session_id="session-1",
        elapsed_seconds=0.1,
        response_text=response_text,
        stdout_text="",
        stderr_text="",
        diagnostics={},
    )


class AutomaticDiscoveryArtifactTests(unittest.TestCase):
    def _run(self, workspace: Path, input_dir: Path):
        plan_path = input_dir / "plan_input.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("{}", encoding="utf-8")
        return run_auto_discovery(
            plan={
                "agent_context": {"workspace_root": str(workspace)},
                "cases": [{"case_id": "case-001"}],
            },
            plan_path=plan_path,
            input_dir=input_dir,
            task_id="EV-DISCOVERY-1",
        )

    def test_invalid_first_candidate_is_corrected_once(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-correction-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            calls = 0

            def fake_agent(**kwargs):
                nonlocal calls
                calls += 1
                kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
                text = (
                    '{"schema_version": "clawevolve.plan.discovery.v1" '
                    if calls == 1
                    else json.dumps(discovery_payload(workspace), ensure_ascii=False)
                )
                kwargs["output_path"].write_text(text, encoding="utf-8")
                return agent_result()

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message",
                side_effect=fake_agent,
            ):
                result = self._run(workspace, input_dir)

            self.assertEqual(calls, 2)
            self.assertEqual(result.targets, [TARGET])
            self.assertFalse((input_dir / "discovery.candidate.json").exists())
            self.assertTrue((input_dir / "discovery.invalid.attempt-1.json").is_file())
            self.assertEqual(
                json.loads((input_dir / "discovery.json").read_text(encoding="utf-8"))[
                    "merged_targets"
                ],
                [TARGET],
            )

    def test_two_invalid_attempts_fail_without_unbounded_retry(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-bounded-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            calls = 0

            def fake_agent(**kwargs):
                nonlocal calls
                calls += 1
                kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
                kwargs["output_path"].write_text("{invalid", encoding="utf-8")
                return agent_result()

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message",
                side_effect=fake_agent,
            ):
                with self.assertRaisesRegex(
                    ValueError, "remained invalid after one bounded correction"
                ):
                    self._run(workspace, input_dir)

            self.assertEqual(calls, 2)
            self.assertFalse((input_dir / "discovery.json").exists())

    def test_no_candidate_or_response_fails_without_speculative_correction(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-empty-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message",
                return_value=agent_result(),
            ) as agent:
                with self.assertRaisesRegex(
                    ValueError, "no repairable structured artifact"
                ):
                    self._run(workspace, input_dir)

            self.assertEqual(agent.call_count, 1)
            self.assertFalse((input_dir / "discovery.json").exists())

    def test_missing_candidate_recovers_from_structured_response(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-response-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            response = (
                "result:\n```json\n"
                + json.dumps(discovery_payload(workspace), ensure_ascii=False)
                + "\n```"
            )

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message",
                return_value=agent_result(response_text=response),
            ) as agent:
                result = self._run(workspace, input_dir)

            self.assertEqual(agent.call_count, 1)
            self.assertTrue(
                any("recovered from agent response" in w for w in result.warnings)
            )
            self.assertTrue((input_dir / "discovery.json").is_file())

    def test_target_boundary_validation_is_reapplied_after_correction(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-boundary-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            calls = 0

            def fake_agent(**kwargs):
                nonlocal calls
                calls += 1
                payload = discovery_payload(workspace, target="../outside.md")
                kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
                kwargs["output_path"].write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
                return agent_result()

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message",
                side_effect=fake_agent,
            ):
                with self.assertRaisesRegex(ValueError, "bounded correction"):
                    self._run(workspace, input_dir)

            self.assertEqual(calls, 2)
            self.assertFalse((input_dir / "discovery.json").exists())

    def test_invalid_reusable_and_stale_candidate_are_quarantined(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-stale-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            input_dir.mkdir(parents=True)
            (input_dir / "discovery.json").write_text("{bad-final", encoding="utf-8")
            (input_dir / "discovery.candidate.json").write_text(
                "{bad-candidate", encoding="utf-8"
            )

            def fake_agent(**kwargs):
                kwargs["output_path"].write_text(
                    json.dumps(discovery_payload(workspace), ensure_ascii=False),
                    encoding="utf-8",
                )
                return agent_result()

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message",
                side_effect=fake_agent,
            ):
                self._run(workspace, input_dir)

            self.assertTrue((input_dir / "discovery.invalid.stale.json").is_file())
            self.assertTrue(
                (
                    input_dir / "discovery.candidate.invalid.stale-candidate.json"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
