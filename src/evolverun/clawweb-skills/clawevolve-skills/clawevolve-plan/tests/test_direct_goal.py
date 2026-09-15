from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.bench.case_contract import _fallback_contract  # noqa: E402
from clawevolve_plan.bench.template_builder import render_templates  # noqa: E402
from clawevolve_plan.bench.split import (  # noqa: E402
    assign_train_test_splits,
    build_split_audit,
)
from clawevolve_plan.direct_goal.schema import (  # noqa: E402
    goal_digest,
    normalize_direct_goal_source,
    validate_direct_goal_payload,
)
from clawevolve_plan.input.context import build_planning_context  # noqa: E402
from clawevolve_plan.direct_goal.service import (  # noqa: E402
    DirectGoalResult,
    build_direct_goal_plan,
)
from clawevolve_plan.pipeline.fresh import _load_and_validate_inputs  # noqa: E402
from clawevolve_plan.pipeline.inputs import (  # noqa: E402
    _find_plan_path_or_none,
    _validate_discovery_inputs,
)
from clawevolve_plan.spec.builder import (  # noqa: E402
    build_objective_document,
    build_spec,
)
from clawevolve_plan.spec.renderer import (  # noqa: E402
    render_goal_markdown,
    render_markdown,
)
from clawevolve_plan.spec.contract import (  # noqa: E402
    validate_objective_markdown,
    validate_spec_markdown,
)


GOAL = "新增数据预处理 Skill，使目标任务完成率达到 90%"
TARGET = "skills/data-preprocessing/SKILL.md"
GREENFIELD_GOAL = (
    "创建一个能统计bot每天开启了多少新session的skill，创建5个bench templates用于验证skill的正确性，"
    "并且最终任务成功率要达到100%"
)
CREATION_SCOPE = "skills/skills-local"
REFERENCE_FILE = "skills/skills-local/example-skill/SKILL.md"
PLANNED_SKILL = "skills/skills-local/session-stats/SKILL.md"


def direct_payload(workspace: Path, *, goal: str = GOAL) -> dict:
    return {
        "schema_version": "clawevolve.plan.direct-goal.v1",
        "goal_digest": goal_digest(goal),
        "workspace_root": str(workspace),
        "goal_analysis": {
            "raw_goal": goal,
            "task_scope": "数据预处理任务",
            "desired_outcome": "目标任务完成率达到 90%",
            "required_capabilities": ["识别并执行数据预处理步骤"],
            "quality_requirements": ["完成率达到 90%"],
            "constraints": ["不修改 judge"],
            "requested_deliverables": ["新增数据预处理 Skill"],
        },
        "prospective_cases": [
            {
                "case_id": "goal-case-001",
                "case_type": "prospective",
                "query": "请完成数据预处理任务 A",
                "scenario": "验证核心数据预处理能力",
                "expected_behavior": "完成处理并返回真实结果",
                "forbidden_behavior": ["不得编造处理结果"],
                "success_criteria": ["完成全部处理步骤"],
                "scoring_hints": ["重点检查结果完整性"],
                "failure_mode": "missing_skill_capability",
            }
        ],
        "discovery": {
            "schema_version": "clawevolve.plan.discovery.v1",
            "workspace_root": str(workspace),
            "analysis_summary": {
                "diagnosed_problem": "无 Diagnose；用户要求新增能力",
                "environment_root_cause": "当前 Skill 缺少任务指导",
                "optimization_strategy": "窄范围增强目标 Skill",
            },
            "case_findings": [
                {
                    "case_id": "goal-case-001",
                    "case_type": "prospective",
                    "failure_mode": "missing_skill_capability",
                    "symptom": "无法稳定完成任务",
                    "evidence": ["用户 goal", TARGET],
                    "inspected_files": [TARGET],
                    "environment_analysis": "目标 Skill 内容不足",
                    "optimization_ideas": ["补充工作流和失败恢复"],
                    "root_cause_hypothesis": "缺少明确任务契约",
                    "confidence": "high",
                }
            ],
            "target_findings": [
                {
                    "path": TARGET,
                    "target_type": "skill",
                    "reason": "目标 Skill 是最窄修改入口",
                    "current_gap": "缺少完整任务流程",
                    "proposed_change": "补充触发、执行和验证规则",
                    "related_case_ids": ["goal-case-001"],
                    "failure_modes": ["missing_skill_capability"],
                    "confidence": "high",
                }
            ],
            "merged_targets": [TARGET],
            "forbidden_boundary_check": {
                "passed": True,
                "checked": ["no judge changes"],
                "notes": "仅修改目标 Skill",
            },
            "warnings": [],
        },
    }


def make_workspace(root: Path) -> Path:
    workspace = root / ".openclaw" / "workspace"
    target = workspace / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("# Data preprocessing\n", encoding="utf-8")
    return workspace


def make_greenfield_workspace(root: Path) -> Path:
    workspace = root / ".openclaw" / "workspace"
    scope = workspace / CREATION_SCOPE
    scope.mkdir(parents=True)
    reference = workspace / REFERENCE_FILE
    reference.parent.mkdir(parents=True)
    reference.write_text("# Example skill\n", encoding="utf-8")
    return workspace


def greenfield_payload(workspace: Path, *, case_count: int = 5) -> dict:
    payload = direct_payload(workspace, goal=GREENFIELD_GOAL)
    cases = []
    findings = []
    for index in range(1, case_count + 1):
        case_id = f"goal-case-{index:03d}"
        cases.append(
            {
                "case_id": case_id,
                "case_type": "prospective",
                "query": f"统计 2026-08-{index:02d} 新开启的 session 数量",
                "scenario": f"验证第 {index} 个 session 统计场景",
                "expected_behavior": "返回指定日期的新 session 数量和可核验依据",
                "forbidden_behavior": ["不得编造 session 数量"],
                "success_criteria": ["数量正确", "统计口径明确"],
                "scoring_hints": ["核对日期边界与去重逻辑"],
                "failure_mode": "missing_skill_capability",
            }
        )
        findings.append(
            {
                "case_id": case_id,
                "case_type": "prospective",
                "failure_mode": "missing_skill_capability",
                "symptom": "缺少按日统计 session 的能力",
                "evidence": [GREENFIELD_GOAL, CREATION_SCOPE, REFERENCE_FILE],
                "inspected_files": [CREATION_SCOPE, REFERENCE_FILE],
                "environment_analysis": "现有目录可承载新 Skill，参考 Skill 仅用于读取模式",
                "optimization_ideas": ["后续 patch 创建 session-stats Skill"],
                "root_cause_hypothesis": "当前 workspace 尚无对应 Skill",
                "confidence": "high",
            }
        )
    payload["prospective_cases"] = cases
    payload["discovery"].update(
        {
            "case_findings": findings,
            "target_findings": [
                {
                    "path": CREATION_SCOPE,
                    "target_type": "creation_scope",
                    "reason": "已检查的最窄现有 Skill 父目录",
                    "current_gap": "尚无 session-stats Skill",
                    "proposed_change": "后续 patch 在范围内创建计划交付物",
                    "related_case_ids": [item["case_id"] for item in cases],
                    "failure_modes": ["missing_skill_capability"],
                    "confidence": "high",
                }
            ],
            "merged_targets": [CREATION_SCOPE],
            "reference_files": [REFERENCE_FILE],
            "planned_deliverables": [
                {
                    "path": PLANNED_SKILL,
                    "operation": "create",
                    "creation_scope": CREATION_SCOPE,
                    "deliverable_type": "skill",
                    "reason": "用户明确要求创建 session 统计 Skill",
                }
            ],
        }
    )
    return payload


def make_args(root: Path, *, run_dir: str, goal: str = GOAL) -> argparse.Namespace:
    return argparse.Namespace(
        run_dir=run_dir,
        task_id="EV-DIRECT-1",
        evolve_results_dir=str(root / "results"),
        discovery_notes="",
        target=[],
        plan_source_path="",
        goal=goal,
        bot_id="bot-direct",
    )


def direct_context(payload, *, task_id: str) -> dict:
    source = normalize_direct_goal_source(
        payload, task_id=task_id, bot_id="bot-direct"
    )
    return build_planning_context(source, "/tmp/source.json")


def diagnose_source() -> dict:
    return {
        "schema_version": "plan-source/v2",
        "generated_at": "2026-08-25T00:00:00Z",
        "source": {
            "type": "diagnose",
            "id": "diagnose:EV-1",
            "producer": "clawevolve-diagnose",
            "bot_id": "bot-direct",
            "version": "2",
        },
        "problem": {"title": "diagnosed issue", "user_guidance": None},
        "cases": [
            {
                "case_id": "c1",
                "case_type": "bad",
                "session_id": "session-1",
                "query": "q",
                "evidence": {"items": ["e"]},
                "analysis": {"evolution_failure_mode": "tool_execution_failure"},
            }
        ],
        "analysis": {"case_distribution": {"total": 1}, "root_cause_clusters": []},
        "planning_hints": {},
        "extensions": {"diagnose": {"artifacts": {}, "agent_context": {}}},
    }


class DirectGoalSchemaTests(unittest.TestCase):
    def test_payload_normalizes_to_prospective_plan_without_session_evidence(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-schema-") as td:
            workspace = make_workspace(Path(td))
            validated = validate_direct_goal_payload(
                direct_payload(workspace), goal=GOAL, workspace_root=workspace
            )
            plan = direct_context(validated, task_id="EV-1")

        self.assertEqual(plan["input_mode"], "direct_goal")
        self.assertEqual(plan["cases"][0]["case_type"], "prospective")
        self.assertIsNone(plan["cases"][0]["session_id"])
        self.assertEqual(plan["user_intent"]["intent_text"], GOAL)

    def test_rewritten_goal_is_allowed_but_empty_goal_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="plan-goal-rewrite-") as td:
            workspace = make_workspace(Path(td))
            goal = '当用户说“你好”时，固定回复“你好，我是Teamclaw，很高兴为你服务！”，不要添加其他内容。'
            for rewrite in (goal.replace('“', '"').replace('”', '"'),
                            '针对你好这条输入，只输出指定的Teamclaw问候语，不附加说明。'):
                payload = direct_payload(workspace, goal=goal)
                payload["goal_analysis"]["raw_goal"] = rewrite
                result = validate_direct_goal_payload(payload, goal=goal, workspace_root=workspace)
                self.assertEqual(result.goal_analysis["raw_goal"], rewrite)
            payload["goal_analysis"]["raw_goal"] = " "
            with self.assertRaisesRegex(ValueError, "raw_goal is required"):
                validate_direct_goal_payload(payload, goal=goal, workspace_root=workspace)

    def test_payload_rejects_goal_digest_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-digest-") as td:
            workspace = make_workspace(Path(td))
            payload = direct_payload(workspace)
            payload["goal_digest"] = "stale"
            with self.assertRaisesRegex(ValueError, "goal_digest"):
                validate_direct_goal_payload(
                    payload, goal=GOAL, workspace_root=workspace
                )

    def test_payload_rejects_missing_or_out_of_workspace_target(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-target-") as td:
            workspace = make_workspace(Path(td))
            payload = direct_payload(workspace)
            payload["discovery"]["merged_targets"] = ["../outside.md"]
            payload["discovery"]["target_findings"][0]["path"] = "../outside.md"
            with self.assertRaisesRegex(ValueError, "outside workspace|path traversal"):
                validate_direct_goal_payload(
                    payload, goal=GOAL, workspace_root=workspace
                )

    def test_prospective_fallback_contract_uses_goal_contract_not_history(self):
        case = {
            "case_id": "goal-case-001",
            "case_type": "prospective",
            "query": "执行任务 A",
            "expected_behavior": "返回结果 A",
            "success_criteria": ["结果完整"],
            "scoring_hints": ["检查完整性"],
            "forbidden_behavior": ["不得编造"],
            "evolution_failure_mode": "missing_capability",
        }
        contract = _fallback_contract(case, GOAL, {"intent_text": GOAL})

        self.assertEqual(contract["source_session_id"], "")
        self.assertEqual(contract["failure_contract"]["historical_failure_mode"], "")
        self.assertEqual(contract["task_contract"]["required_outcomes"], ["结果完整"])
        self.assertNotIn("历史失败模式", json.dumps(contract, ensure_ascii=False))

    def test_prospective_split_audit_does_not_require_bad_or_good_history(self):
        cases = assign_train_test_splits(
            [
                {
                    "case_id": "goal-case-001",
                    "case_type": "prospective",
                    "query": "任务 A",
                },
                {
                    "case_id": "goal-case-002",
                    "case_type": "prospective",
                    "query": "任务 B",
                },
            ]
        )
        audit = build_split_audit(cases)

        self.assertNotIn("no_bad_case_for_failure_optimization", audit["warnings"])
        self.assertNotIn("no_good_regression_case", audit["warnings"])
        self.assertEqual(
            audit["summary"]["prospectiveTrainCount"]
            + audit["summary"]["prospectiveTestCount"],
            2,
        )


class DirectGoalGreenfieldAcceptanceTests(unittest.TestCase):
    def test_direct_goal_normalizes_safe_absolute_deliverable_and_allows_sparse_findings(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-lenient-") as td:
            workspace = make_greenfield_workspace(Path(td))
            payload = greenfield_payload(workspace)
            payload["discovery"]["target_findings"].append(
                {
                    "path": str(workspace / REFERENCE_FILE),
                    "target_type": "reference",
                }
            )
            payload["discovery"]["planned_deliverables"][0]["path"] = str(
                workspace / PLANNED_SKILL
            )
            validated = validate_direct_goal_payload(
                payload,
                goal=GREENFIELD_GOAL,
                workspace_root=workspace,
            )

        self.assertEqual(
            validated.discovery.raw["planned_deliverables"][0]["path"],
            PLANNED_SKILL,
        )

    def test_direct_goal_still_rejects_absolute_deliverable_outside_workspace(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-outside-") as td:
            workspace = make_greenfield_workspace(Path(td))
            payload = greenfield_payload(workspace)
            payload["discovery"]["planned_deliverables"][0]["path"] = (
                "/tmp/outside/SKILL.md"
            )
            with self.assertRaisesRegex(ValueError, "workspace-relative|outside"):
                validate_direct_goal_payload(
                    payload,
                    goal=GREENFIELD_GOAL,
                    workspace_root=workspace,
                )

    def test_greenfield_creation_scope_reference_and_future_deliverable_validate(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-greenfield-") as td:
            workspace = make_greenfield_workspace(Path(td))
            validated = validate_direct_goal_payload(
                greenfield_payload(workspace),
                goal=GREENFIELD_GOAL,
                workspace_root=workspace,
            )
            plan = direct_context(validated, task_id="EV-GREENFIELD")
            planned_path_exists = (workspace / PLANNED_SKILL).exists()

        self.assertFalse(
            planned_path_exists, "Plan validation must not create future deliverables"
        )
        self.assertEqual(validated.discovery.targets, [CREATION_SCOPE])
        self.assertEqual(plan["creation_scopes"], [CREATION_SCOPE])
        self.assertEqual(plan["reference_files"], [REFERENCE_FILE])
        self.assertEqual(plan["planned_deliverables"][0]["path"], PLANNED_SKILL)
        self.assertEqual(
            plan["default_optimization_goal"]["target_task_success_rate"], 1.0
        )

    def test_future_leaf_cannot_be_an_ordinary_target(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-future-target-") as td:
            workspace = make_greenfield_workspace(Path(td))
            payload = greenfield_payload(workspace)
            payload["discovery"]["merged_targets"] = [PLANNED_SKILL]
            payload["discovery"]["target_findings"][0]["path"] = PLANNED_SKILL
            payload["discovery"]["target_findings"][0]["target_type"] = "skill"
            with self.assertRaisesRegex(ValueError, "does not exist"):
                validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )

    def test_openversion_allows_skills_only_as_existing_creation_scope(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-open-scope-") as td:
            workspace = make_greenfield_workspace(Path(td))
            payload = greenfield_payload(workspace)
            payload["discovery"]["merged_targets"] = ["skills"]
            payload["discovery"]["reference_files"] = []
            payload["discovery"]["target_findings"][0]["path"] = "skills"
            payload["discovery"]["planned_deliverables"][0].update(
                {
                    "path": "skills/session-stats/SKILL.md",
                    "creation_scope": "skills",
                }
            )

            with patch.dict(os.environ, {"CLAWWEB_VERSION": "openversion"}):
                validated = validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )
                plan = direct_context(validated, task_id="EV-OPEN-SCOPE")
                _validate_discovery_inputs(
                    f"direct_goal inspected `skills` for {GREENFIELD_GOAL}",
                    ["skills"],
                    plan,
                )

            self.assertEqual(validated.discovery.targets, ["skills"])

            with (
                patch.dict(os.environ, {"CLAWWEB_VERSION": "internalversion"}),
                self.assertRaisesRegex(ValueError, "too broad"),
            ):
                validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )

    def test_planned_deliverable_requires_declared_directory_creation_scope(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-scope-") as td:
            workspace = make_greenfield_workspace(Path(td))
            payload = greenfield_payload(workspace)
            payload["discovery"]["planned_deliverables"][0]["creation_scope"] = (
                "skills/other"
            )
            with self.assertRaisesRegex(ValueError, "must be one of merged_targets"):
                validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )

            payload = greenfield_payload(workspace)
            payload["discovery"]["target_findings"][0]["target_type"] = "skill"
            with self.assertRaisesRegex(ValueError, "target_type=creation_scope"):
                validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )

            payload = greenfield_payload(workspace)
            payload["discovery"]["merged_targets"] = [REFERENCE_FILE]
            payload["discovery"]["reference_files"] = []
            payload["discovery"]["target_findings"][0]["path"] = REFERENCE_FILE
            payload["discovery"]["planned_deliverables"][0]["creation_scope"] = (
                REFERENCE_FILE
            )
            payload["discovery"]["planned_deliverables"][0]["path"] = (
                REFERENCE_FILE + "/child.md"
            )
            with self.assertRaisesRegex(ValueError, "must be an existing directory"):
                validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )

    def test_planned_deliverable_rejects_traversal_out_of_scope_and_existing_path(self):
        invalid_paths = (
            "../outside/SKILL.md",
            "skills/unrelated/session-stats/SKILL.md",
            REFERENCE_FILE,
        )
        for invalid_path in invalid_paths:
            with (
                self.subTest(path=invalid_path),
                tempfile.TemporaryDirectory(prefix="plan-direct-boundary-") as td,
            ):
                workspace = make_greenfield_workspace(Path(td))
                payload = greenfield_payload(workspace)
                payload["discovery"]["planned_deliverables"][0]["path"] = invalid_path
                expected = (
                    "already exists"
                    if invalid_path == REFERENCE_FILE
                    else "path traversal|outside creation_scope|outside workspace"
                )
                with self.assertRaisesRegex(ValueError, expected):
                    validate_direct_goal_payload(
                        payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                    )

    def test_reference_files_are_existing_read_only_and_disjoint_from_targets(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-reference-") as td:
            workspace = make_greenfield_workspace(Path(td))
            payload = greenfield_payload(workspace)
            payload["discovery"]["reference_files"] = [CREATION_SCOPE]
            with self.assertRaisesRegex(ValueError, "must not also be merged_targets"):
                validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )

            payload = greenfield_payload(workspace)
            payload["discovery"]["reference_files"] = [
                "skills/skills-local/missing/SKILL.md"
            ]
            with self.assertRaisesRegex(ValueError, "does not exist"):
                validate_direct_goal_payload(
                    payload, goal=GREENFIELD_GOAL, workspace_root=workspace
                )

    def test_goal_requested_bench_count_must_be_exact(self):
        for case_count in (1, 4, 6):
            with (
                self.subTest(case_count=case_count),
                tempfile.TemporaryDirectory(prefix="plan-direct-count-") as td,
            ):
                workspace = make_greenfield_workspace(Path(td))
                with self.assertRaisesRegex(ValueError, "exactly 5 cases"):
                    validate_direct_goal_payload(
                        greenfield_payload(workspace, case_count=case_count),
                        goal=GREENFIELD_GOAL,
                        workspace_root=workspace,
                    )

    def test_spec_and_five_templates_preserve_greenfield_contract(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-spec-") as td:
            root = Path(td)
            workspace = make_greenfield_workspace(root)
            validated = validate_direct_goal_payload(
                greenfield_payload(workspace),
                goal=GREENFIELD_GOAL,
                workspace_root=workspace,
            )
            plan = direct_context(validated, task_id="EV-GREENFIELD")
            spec = build_spec(
                plan,
                goal_override=GREENFIELD_GOAL,
                discovery_notes="direct_goal\n" + GREENFIELD_GOAL,
                target_files=[CREATION_SCOPE],
            )
            spec_md = render_markdown(spec)
            template_dir, _, names, manifest = render_templates(
                plan, root / "output", goal_text=GREENFIELD_GOAL
            )
            task_file_count = len(list(template_dir.glob("**/task_*.md")))

        self.assertEqual(spec["goal"]["target_task_success_rate"], 1.0)
        self.assertEqual(spec["allowed_update_targets"], [])
        self.assertEqual(spec["allowed_creation_scopes"], [CREATION_SCOPE])
        self.assertEqual(spec["reference_files"], [REFERENCE_FILE])
        self.assertEqual(spec["planned_deliverables"][0]["path"], PLANNED_SKILL)
        self.assertIn("Plan 本身不创建这些文件", spec["patch_generator_instruction"])
        self.assertIn("只读参考", spec["patch_generator_instruction"])
        self.assertIn("### 允许创建范围", spec_md)
        self.assertIn("### 只读参考文件", spec_md)
        self.assertIn("### 计划交付物（由后续 patch/evolve 创建）", spec_md)
        self.assertEqual(len(names), 5)
        self.assertEqual(manifest["template_count"], 5)
        self.assertEqual(task_file_count, 5)


class DirectGoalServiceTests(unittest.TestCase):
    def test_agent_output_materializes_all_direct_goal_inputs(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-service-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = workspace / "clawevolve_results" / "EV-1" / "plan" / "input"

            def fake_agent(**kwargs):
                kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
                kwargs["output_path"].write_text(
                    json.dumps(direct_payload(workspace), ensure_ascii=False),
                    encoding="utf-8",
                )
                return SimpleNamespace(
                    status="success",
                    elapsed_seconds=0.1,
                    response_text="",
                    stdout_text="",
                    diagnostics={},
                )

            with (
                patch.dict(
                    os.environ,
                    {"CLAWEVOLVE_INVOCATION_CWD": str(workspace)},
                    clear=False,
                ),
                patch(
                    "clawevolve_plan.direct_goal.service.run_openclaw_agent_message",
                    side_effect=fake_agent,
                ),
            ):
                result = build_direct_goal_plan(
                    goal=GOAL, task_id="EV-1", bot_id="bot-direct", input_dir=input_dir
                )

            self.assertTrue(result.plan_path.is_file())
            descriptor = json.loads(
                (input_dir / "source-descriptor.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                descriptor["descriptorVersion"], "plan-source-descriptor/v2"
            )
            self.assertEqual(descriptor["sourceType"], "direct_goal")
            self.assertEqual(descriptor["schemaVersion"], "plan-source/v2")
            self.assertTrue(result.discovery_json_path.is_file())
            self.assertIn("direct_goal", result.notes_path.read_text(encoding="utf-8"))
            self.assertEqual(result.targets, [TARGET])

    def test_no_candidate_or_response_fails_without_speculative_correction(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-empty-artifact-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = workspace / "clawevolve_results" / "EV-1" / "plan" / "input"

            with (
                patch.dict(
                    os.environ,
                    {"CLAWEVOLVE_INVOCATION_CWD": str(workspace)},
                    clear=False,
                ),
                patch(
                    "clawevolve_plan.direct_goal.service.run_openclaw_agent_message",
                    return_value=SimpleNamespace(
                        status="success",
                        elapsed_seconds=0.1,
                        response_text="",
                        stdout_text="",
                        diagnostics={},
                    ),
                ) as agent,
            ):
                with self.assertRaisesRegex(
                    ValueError, "no repairable structured artifact"
                ):
                    build_direct_goal_plan(
                        goal=GOAL, task_id="EV-1", bot_id="bot-direct", input_dir=input_dir
                    )

            self.assertEqual(agent.call_count, 1)
            self.assertFalse((input_dir / "direct_goal.json").exists())

    def test_invalid_first_candidate_is_corrected_once(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-correction-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = workspace / "clawevolve_results" / "EV-1" / "plan" / "input"
            calls = 0

            def fake_agent(**kwargs):
                nonlocal calls
                calls += 1
                kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
                text = (
                    '{"schema_version": "clawevolve.plan.direct-goal.v1" '
                    if calls == 1
                    else json.dumps(direct_payload(workspace), ensure_ascii=False)
                )
                kwargs["output_path"].write_text(text, encoding="utf-8")
                return SimpleNamespace(
                    status="success",
                    elapsed_seconds=0.1,
                    response_text="",
                    stdout_text="",
                    diagnostics={},
                )

            with (
                patch.dict(
                    os.environ,
                    {"CLAWEVOLVE_INVOCATION_CWD": str(workspace)},
                    clear=False,
                ),
                patch(
                    "clawevolve_plan.direct_goal.service.run_openclaw_agent_message",
                    side_effect=fake_agent,
                ),
            ):
                result = build_direct_goal_plan(
                    goal=GOAL, task_id="EV-1", bot_id="bot-direct", input_dir=input_dir
                )

            self.assertEqual(calls, 2)
            self.assertEqual(result.targets, [TARGET])
            self.assertFalse((input_dir / "direct_goal.candidate.json").exists())
            self.assertTrue(
                (input_dir / "direct_goal.invalid.attempt-1.json").is_file()
            )
            canonical = json.loads(
                (input_dir / "direct_goal.json").read_text(encoding="utf-8")
            )
            self.assertEqual(canonical["goal_analysis"]["raw_goal"], GOAL)
            self.assertEqual(canonical["original_goal"], GOAL)
            self.assertEqual(canonical["discovery"]["merged_targets"], [TARGET])

    def test_two_invalid_attempts_fail_without_unbounded_retry(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-bounded-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = workspace / "clawevolve_results" / "EV-1" / "plan" / "input"
            calls = 0

            def fake_agent(**kwargs):
                nonlocal calls
                calls += 1
                kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
                kwargs["output_path"].write_text("{invalid", encoding="utf-8")
                return SimpleNamespace(
                    status="success",
                    elapsed_seconds=0.1,
                    response_text="",
                    stdout_text="",
                    diagnostics={},
                )

            with (
                patch.dict(
                    os.environ,
                    {"CLAWEVOLVE_INVOCATION_CWD": str(workspace)},
                    clear=False,
                ),
                patch(
                    "clawevolve_plan.direct_goal.service.run_openclaw_agent_message",
                    side_effect=fake_agent,
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError, "remained invalid after one bounded correction"
                ):
                    build_direct_goal_plan(
                        goal=GOAL, task_id="EV-1", bot_id="bot-direct", input_dir=input_dir
                    )

            self.assertEqual(calls, 2)
            self.assertFalse((input_dir / "direct_goal.json").exists())

    def test_invalid_reusable_direct_goal_is_quarantined_before_regeneration(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-stale-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = workspace / "clawevolve_results" / "EV-1" / "plan" / "input"
            input_dir.mkdir(parents=True)
            (input_dir / "direct_goal.json").write_text("{bad-final", encoding="utf-8")
            (input_dir / "direct_goal.candidate.json").write_text(
                "{bad-candidate", encoding="utf-8"
            )

            def fake_agent(**kwargs):
                kwargs["output_path"].write_text(
                    json.dumps(direct_payload(workspace), ensure_ascii=False),
                    encoding="utf-8",
                )
                return SimpleNamespace(
                    status="success",
                    elapsed_seconds=0.1,
                    response_text="",
                    stdout_text="",
                    diagnostics={},
                )

            with (
                patch.dict(
                    os.environ,
                    {"CLAWEVOLVE_INVOCATION_CWD": str(workspace)},
                    clear=False,
                ),
                patch(
                    "clawevolve_plan.direct_goal.service.run_openclaw_agent_message",
                    side_effect=fake_agent,
                ),
            ):
                build_direct_goal_plan(
                    goal=GOAL, task_id="EV-1", bot_id="bot-direct", input_dir=input_dir
                )

            self.assertTrue((input_dir / "direct_goal.invalid.stale.json").is_file())
            self.assertTrue(
                (
                    input_dir / "direct_goal.candidate.invalid.stale-candidate.json"
                ).is_file()
            )


class DirectGoalPipelineTests(unittest.TestCase):
    def test_diagnose_input_has_priority_and_does_not_call_direct_goal(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-priority-") as td:
            root = Path(td)
            run_dir = root / "diagnose"
            run_dir.mkdir()
            plan_path = run_dir / "plan-source.json"
            plan_path.write_text(json.dumps(diagnose_source()), encoding="utf-8")
            notes = root / "notes.md"
            notes.write_text(
                f"inspected `{TARGET}` for tool_execution_failure\n",
                encoding="utf-8",
            )
            args = make_args(root, run_dir=str(run_dir))
            args.plan_source_path = str(plan_path)
            args.discovery_notes = str(notes)
            args.target = [TARGET]

            with patch(
                "clawevolve_plan.pipeline.fresh.build_direct_goal_plan"
            ) as direct:
                plan, manifest, _, _, _ = _load_and_validate_inputs(
                    args, root / "plan" / "input", root / "plan" / "output"
                )

            direct.assert_not_called()
            self.assertEqual(plan["cases"][0]["case_id"], "c1")
            self.assertEqual(manifest["items"][0]["label"], "plan_source")

    def test_missing_diagnose_uses_direct_goal_result(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-pipeline-") as td:
            root = Path(td)
            input_dir = root / "plan" / "input"
            input_dir.mkdir(parents=True)
            plan_path = input_dir / "source.json"
            workspace = make_workspace(root)
            validated = validate_direct_goal_payload(
                direct_payload(workspace), goal=GOAL, workspace_root=workspace
            )
            plan = normalize_direct_goal_source(
                validated, task_id="EV-DIRECT-1", bot_id="bot-direct"
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            notes_path = input_dir / "discovery_notes.md"
            notes_path.write_text(
                f"# Discovery Notes\n\n- direct_goal\n- {GOAL}\n- `{TARGET}`\n",
                encoding="utf-8",
            )
            direct_result = DirectGoalResult(
                plan=plan,
                plan_path=plan_path,
                discovery_json_path=input_dir / "discovery.json",
                notes_path=notes_path,
                targets=[TARGET],
                warnings=[],
            )
            args = make_args(root, run_dir=str(root / "missing"))

            with (
                patch(
                    "clawevolve_plan.pipeline.fresh.build_direct_goal_plan",
                    return_value=direct_result,
                ) as direct,
                patch("clawevolve_plan.pipeline.fresh.run_auto_discovery") as discovery,
            ):
                loaded, manifest, _, _, archived_path = _load_and_validate_inputs(
                    args, input_dir, root / "plan" / "output"
                )

            direct.assert_called_once()
            discovery.assert_not_called()
            self.assertEqual(loaded["input_mode"], "direct_goal")
            self.assertEqual(manifest["items"][0]["label"], "plan_source")
            self.assertEqual(archived_path, plan_path)

    def test_missing_diagnose_and_empty_goal_fails_explicitly(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-empty-") as td:
            root = Path(td)
            args = make_args(root, run_dir=str(root / "missing"), goal="")
            with self.assertRaisesRegex(ValueError, "--goal is required"):
                _load_and_validate_inputs(
                    args, root / "plan" / "input", root / "plan" / "output"
                )

    def test_direct_goal_source_is_not_rediscovered_as_diagnose_source(self):
        with tempfile.TemporaryDirectory(prefix="plan-direct-search-") as td:
            root = Path(td)
            direct_path = root / "plan" / "input" / "source.json"
            direct_path.parent.mkdir(parents=True)
            direct_path.write_text("{}", encoding="utf-8")

            self.assertIsNone(_find_plan_path_or_none(str(root)))

            diagnose_path = root / "diagnose" / "output" / "plan-source.json"
            diagnose_path.parent.mkdir(parents=True)
            diagnose_path.write_text("{}", encoding="utf-8")
            self.assertEqual(_find_plan_path_or_none(str(root)), diagnose_path)

    def test_direct_goal_discovery_requires_goal_and_mode_in_notes(self):
        plan = {
            "input_mode": "direct_goal",
            "user_intent": {"intent_text": GOAL},
            "goal_context": {"raw_goal": GOAL},
            "cases": [{"case_id": "goal-case-001"}],
        }
        with self.assertRaisesRegex(ValueError, "direct_goal"):
            _validate_discovery_inputs(f"{GOAL}\n`{TARGET}`", [TARGET], plan)

    def test_direct_goal_fallback_documents_use_prospective_gates_without_fake_history(
        self,
    ):
        with tempfile.TemporaryDirectory(prefix="plan-direct-docs-") as td:
            workspace = make_workspace(Path(td))
            payload = validate_direct_goal_payload(
                direct_payload(workspace), goal=GOAL, workspace_root=workspace
            )
            plan = direct_context(payload, task_id="EV-1")
            spec = build_spec(
                plan,
                goal_override=GOAL,
                discovery_notes=f"direct_goal\n{GOAL}\n`{TARGET}`",
                target_files=[TARGET],
            )
            objective = build_objective_document(spec)
            objective_md = render_goal_markdown(objective)
            spec_md = render_markdown(spec)

        self.assertIn("Prospective case 通过率", objective_md)
        self.assertNotIn("Good regression", objective_md)
        validate_objective_markdown(objective_md, objective)
        validate_spec_markdown(spec_md, spec)
        self.assertIn("prospective cases", spec_md)
        self.assertIn("## Appendix B: Diagnose Evidence and Agentic Discovery", spec_md)
        self.assertNotIn("个诊断样本", spec_md)
        self.assertNotIn("初始版本由 clawevolve-diagnose", spec_md)


if __name__ == "__main__":
    unittest.main()
