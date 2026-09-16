from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.intent import normalize_primary_metric, normalize_user_intent  # noqa: E402
from clawevolve_plan.pipeline.step_report_payload import build_step_report_output  # noqa: E402
from clawevolve_plan.spec.builder import build_objective_document, build_spec  # noqa: E402
from clawevolve_plan.spec.renderer import render_goal_markdown, render_markdown  # noqa: E402
from clawevolve_plan.spec.contract import (  # noqa: E402
    validate_objective_markdown,
    validate_spec_markdown,
)


DIAGNOSE_INTENT = (
    "扫描20260803-20260807的历史session，分析语雀MCP问题，"
    "输出5个有明确session证据的bad case"
)
TARGET = "skills/skills-local/data-preprocessing/scripts/yuque_knowledge_sync.py"


def diagnose_plan(*, domain_status: str = "skipped") -> dict:
    return {
        "schema_version": "clawevolve.plan-input.v1",
        "input_mode": "diagnose",
        "bot_id": "bot-zijin",
        "user_intent": {
            "raw_request": DIAGNOSE_INTENT,
            "intent_text": DIAGNOSE_INTENT,
            "confidence": 0.95,
        },
        "default_optimization_goal": {
            "goal_text": "默认提升任务成功率",
            "target_task_success_rate": 0.9,
            "regression_drop_max": 0.02,
            "case_timeout_seconds": 600,
            "max_iterations": 10,
        },
        "cases": [{"case_id": f"case-{index}"} for index in range(5)],
        "root_cause_clusters": [
            {
                "evolution_failure_mode": "tool_execution_failure",
                "affected_case_count": 5,
                "example_case_ids": ["case-1"],
                "problem_analysis": "语雀 MCP 调用失败。",
                "optimization_goal": "工具失败后安全降级。",
                "allowed_update_targets_hint": [
                    "tool error handling",
                    "retry/backoff policy",
                ],
            }
        ],
        "case_distribution": {"total": 5, "bad": 5},
        "artifacts": {
            "diagnosis_jsonl": "/tmp/run/diagnosis.jsonl",
            "analysis_report_md": "/tmp/run/report.md",
            "log_file": "/tmp/run/verbose.log",
        },
        "agent_context": {
            "workspace": "/home/admin/.openclaw/workspace",
            "agent_id": "discovery-agent",
            "agents": [f"agent-{index}" for index in range(30)],
            "session_dirs": [f"/sessions/{index}" for index in range(50)],
            "skill_dirs": ["/skills/local", "/skills/global"],
        },
        "clawweb_domain": {"status": domain_status, "domain_id": "domain-1"},
    }


class GoalNormalizationTests(unittest.TestCase):
    def test_explicit_plan_goal_overrides_diagnose_acquisition_intent(self):
        intent = normalize_user_intent(diagnose_plan(), "mcp调用成功率80%")

        self.assertEqual(intent["intent_text"], "mcp调用成功率80%")
        self.assertEqual(intent["source"], "cli_goal")
        self.assertEqual(intent["source_diagnose_intent"], DIAGNOSE_INTENT)

    def test_direct_goal_rewrite_is_not_diagnose_provenance(self):
        original = '当用户说“你好”时，只回复“你好，我是Teamclaw”。'
        rewrite = original.replace('“', '"').replace('”', '"')
        plan = diagnose_plan()
        plan["input_mode"] = "direct_goal"
        plan["user_intent"] = {"raw_request": rewrite, "intent_text": rewrite}
        intent = normalize_user_intent(plan, original)
        self.assertEqual(intent["intent_text"], original)
        self.assertEqual(intent["source_diagnose_intent"], "")
        self.assertEqual(intent["source_diagnose_raw_request"], "")
        spec = build_spec(plan, goal_override=original, discovery_notes="checked", target_files=[TARGET])
        objective = build_objective_document(spec)
        validate_objective_markdown(render_goal_markdown(objective) + "\n" + rewrite, objective)
        validate_spec_markdown(render_markdown(spec).replace(
            "## 1. Objective Contract", "## 1. Objective Contract\n\n" + rewrite), spec)

    def test_missing_or_diagnose_mode_retains_acquisition_protection(self):
        for mode in (None, "diagnose"):
            with self.subTest(mode=mode):
                plan = diagnose_plan()
                if mode is None:
                    plan.pop("input_mode")
                intent = normalize_user_intent(plan, "mcp调用成功率80%")
                self.assertEqual(intent["source_diagnose_intent"], DIAGNOSE_INTENT)
                self.assertEqual(intent["source_diagnose_raw_request"], DIAGNOSE_INTENT)
                inherited = normalize_user_intent(plan)
                self.assertEqual(inherited["source_diagnose_intent"], DIAGNOSE_INTENT)

    def test_metric_parser_preserves_metric_semantics(self):
        metric = normalize_primary_metric("mcp调用成功率80%")
        self.assertEqual(metric["name"], "mcp_call_success_rate")
        self.assertEqual(metric["display_name"], "MCP 调用成功率")
        self.assertEqual(metric["target"], 0.8)

        task_metric = normalize_primary_metric("任务成功率达到100%")
        self.assertEqual(task_metric["name"], "task_success_rate")
        self.assertEqual(task_metric["target"], 1.0)
        self.assertEqual(normalize_primary_metric("MCP调用成功率要达到80%")["target"], 0.8)

    def test_metric_parser_uses_default_only_without_explicit_metric(self):
        metric = normalize_primary_metric("提高语雀 MCP 稳定性", 0.9)
        self.assertEqual(metric["source"], "default")
        self.assertEqual(metric["target"], 0.9)


class SpecGoalAlignmentTests(unittest.TestCase):
    def _build(self, *, domain_status: str = "skipped"):
        spec = build_spec(
            diagnose_plan(domain_status=domain_status),
            goal_override="mcp调用成功率80%",
            discovery_notes="已检查语雀同步脚本并确认问题边界。",
            target_files=[TARGET],
        )
        objective = build_objective_document(spec)
        return spec, objective, render_markdown(spec), render_goal_markdown(objective)

    def test_spec_uses_current_goal_and_metric_without_diagnose_goal_pollution(self):
        spec, objective, spec_md, objective_md = self._build()

        self.assertEqual(spec["user_intent"]["intent_text"], "mcp调用成功率80%")
        self.assertEqual(spec["acceptance_criteria"]["primary_metric"]["target"], 0.8)
        self.assertNotIn("task_success_rate_min", spec["acceptance_criteria"])
        self.assertEqual(objective["primary_metric"]["name"], "mcp_call_success_rate")
        self.assertNotIn("validation_total_score_min", spec["acceptance_criteria"])
        self.assertNotIn("validation_total_score_min", objective["quality_gates"])
        self.assertIn("MCP 调用成功率 >= 80%", spec_md)
        self.assertIn("MCP 调用成功率 >= 80%", objective_md)
        self.assertNotIn("验证总分", spec_md)
        self.assertNotIn("验证总分", objective_md)
        self.assertNotIn("任务成功率 >= 0.9", spec_md)
        self.assertNotIn(f"用户意图：{DIAGNOSE_INTENT}", spec_md)
        self.assertNotIn(f"所有策略选择必须服务用户意图：{DIAGNOSE_INTENT}", spec_md)

    def test_skipped_upload_is_described_as_local_evaluation(self):
        spec, objective, spec_md, _ = self._build(domain_status="skipped")
        self.assertIn("本轮生成的本地评测集", spec_md)
        self.assertNotIn("面向已上传的 ClawWeb 评测集", spec_md)
        self.assertEqual(objective["primary_metric"]["scope"], "本轮生成的本地评测样本")
        self.assertIn("本轮生成的本地评测集回放验证", spec["patch_generator_instruction"])

    def test_published_upload_is_described_as_published(self):
        _, objective, spec_md, _ = self._build(domain_status="uploaded")
        self.assertIn("已发布的 ClawWeb 评测集", spec_md)
        self.assertEqual(objective["primary_metric"]["scope"], "已发布的 ClawWeb domain 评测样本")

    def test_allowed_targets_are_only_concrete_inspected_targets(self):
        spec, _, spec_md, _ = self._build()
        self.assertEqual(spec["allowed_update_targets"], [TARGET])
        self.assertIn("retry/backoff policy", spec["optimization_topics"])
        allowed_section = spec_md.split("### 允许更新目标", 1)[1].split("### 禁止改动", 1)[0]
        self.assertNotIn("retry/backoff policy", allowed_section)

    def test_appendix_omits_unbounded_agent_and_session_lists(self):
        _, _, spec_md, _ = self._build()
        self.assertNotIn("session_dirs", spec_md)
        self.assertNotIn('"agents"', spec_md)
        self.assertNotIn("verbose.log", spec_md)
        self.assertIn("discovery-agent", spec_md)

    def test_step_report_uses_canonical_primary_metric(self):
        spec, objective, _, _ = self._build()
        report = build_step_report_output(spec, objective, {"templates": []}, {})
        self.assertEqual(report["goal"]["metrics"][0]["key"], "mcp_call_success_rate")
        self.assertEqual(report["goal"]["metrics"][0]["target"], 0.8)
        self.assertNotIn("successCriteria", report["goal"])

    def test_step_report_exposes_verified_published_template_identity(self):
        spec, objective, _, _ = self._build()
        report = build_step_report_output(
            spec,
            objective,
            {"templates": [{"id": "task_case_1", "case_id": "case-1", "split": "train"}]},
            {
                "published": True,
                "verified": True,
                "domains": {
                    "train": {
                        "published": True,
                        "verified": True,
                        "owner_user_id": "197444",
                        "domain_id": "train-domain",
                        "verify_published": {"body": {"data": {"items": [{"templateName": "task_case_1", "publishedVersion": 3}]}}},
                    },
                    "test": {},
                },
            },
        )

        self.assertEqual(report["benchCases"]["items"][0]["template"], {
            "ownerUserId": "197444",
            "domainId": "train-domain",
            "templateName": "task_case_1",
            "version": 3,
        })

    def test_document_contract_rejects_diagnose_intent_as_objective(self):
        spec, objective, spec_md, objective_md = self._build()
        polluted_spec = spec_md.replace(
            "当前优化目标：mcp调用成功率80%",
            f"当前优化目标：mcp调用成功率80%\n- 用户意图：{DIAGNOSE_INTENT}",
            1,
        )
        polluted_objective = objective_md.replace(
            "- 用户优化目标：mcp调用成功率80%",
            f"- 用户优化目标：mcp调用成功率80%\n- 用户意图：{DIAGNOSE_INTENT}",
            1,
        )
        with self.assertRaisesRegex(ValueError, "Diagnose acquisition intent"):
            validate_spec_markdown(polluted_spec, spec)
        with self.assertRaisesRegex(ValueError, "Diagnose acquisition intent"):
            validate_objective_markdown(polluted_objective, objective)


if __name__ == "__main__":
    unittest.main()
