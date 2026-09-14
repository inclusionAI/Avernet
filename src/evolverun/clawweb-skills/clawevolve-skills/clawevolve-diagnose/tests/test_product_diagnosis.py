from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clawevolve_diagnose.artifacts.reporting import (  # noqa: E402
    AnalysisReportBuilder,
    RelatedFileLocator,
)
from clawevolve_diagnose.integration.output import build_execution_output  # noqa: E402
from clawevolve_diagnose.integration.plan_source import build_plan_source  # noqa: E402
from clawevolve_diagnose.intent import parse_preference  # noqa: E402
from clawevolve_diagnose.models import (  # noqa: E402
    CasePreference,
    Diagnosis,
    LlmRuntimeConfig,
    RunResult,
    SessionRow,
)
from clawevolve_diagnose.product import DiagnosisOutcomeBuilder  # noqa: E402
from clawevolve_diagnose.run.analysis_artifact import write_analysis_report  # noqa: E402


def row(session_id: str) -> SessionRow:
    return SessionRow(
        session_id=session_id,
        path=f"/tmp/{session_id}.jsonl",
        bot_id="bot",
        created_at="2026-08-17T10:00:00+08:00",
        first_question="执行任务",
        user_text="执行任务",
        assistant_text="结果",
        tool_text="",
        raw_text="执行任务 结果",
    )


def diagnosis(
    session_id: str,
    *,
    case_type: str = "bad",
    mode: str = "tool_parameter_error",
    confidence: float = 0.9,
    optimization_value: str = "high",
    controllability: str = "high",
) -> Diagnosis:
    return Diagnosis(
        session=row(session_id),
        case_type=case_type,
        symptom_class="FAILED" if case_type == "bad" else "COMPLETED",
        root_cause_class=mode,
        common_problem_key=mode,
        evolution_failure_mode=mode,
        query="执行工具任务",
        root_cause_summary=f"{mode} evidence",
        confidence=confidence,
        optimization_value=optimization_value,
        failure_controllability=controllability,
    )


def outcome(
    diagnoses: list[Diagnosis],
    *,
    preference: CasePreference | None = None,
    rows: list[SessionRow] | None = None,
    selected: list[Diagnosis] | None = None,
    selection_report: dict | None = None,
) -> dict:
    pref = preference or CasePreference(case_limit=max(1, len(diagnoses)))
    return DiagnosisOutcomeBuilder().build(
        rows=rows if rows is not None else [item.session for item in diagnoses],
        diagnoses=diagnoses,
        selected=selected if selected is not None else diagnoses,
        preference=pref,
        selection_report=selection_report or {},
    )


class DiagnosisIntentTests(unittest.TestCase):
    def test_exploratory_request_is_recognized(self) -> None:
        pref, _ = parse_preference("分析最近 sessions 的主要问题", LlmRuntimeConfig())
        self.assertEqual(pref.diagnosis_mode, "exploratory")
        self.assertEqual(pref.hypothesis_text, "")

    def test_hypothesis_request_preserves_claim(self) -> None:
        message = "验证最近 sessions 是否存在工具参数错误"
        pref, _ = parse_preference(message, LlmRuntimeConfig())
        self.assertEqual(pref.diagnosis_mode, "hypothesis")
        self.assertEqual(pref.hypothesis_text, message)


class DiagnosisOutcomeTests(unittest.TestCase):
    def hypothesis_preference(self, case_limit: int) -> CasePreference:
        return CasePreference(
            raw_message="验证是否存在工具参数错误",
            intent_text="验证是否存在工具参数错误",
            diagnosis_mode="hypothesis",
            hypothesis_text="是否存在工具参数错误",
            case_limit=case_limit,
        )

    def test_hypothesis_verdicts_cover_supported_partial_and_not_supported(
        self,
    ) -> None:
        bad = diagnosis("bad")
        good = diagnosis("good", case_type="good", mode="good_regression")

        supported = outcome([bad], preference=self.hypothesis_preference(1))
        partial = outcome([bad, good], preference=self.hypothesis_preference(2))
        not_supported = outcome([good], preference=self.hypothesis_preference(1))

        self.assertEqual(supported["hypothesis_result"]["verdict"], "supported")
        self.assertEqual(partial["hypothesis_result"]["verdict"], "partially_supported")
        self.assertEqual(not_supported["hypothesis_result"]["verdict"], "not_supported")

    def test_hypothesis_ignores_unrelated_failure_modes(self) -> None:
        pref = self.hypothesis_preference(1)
        pref.target_failure_modes = ["tool_parameter_error"]
        unrelated = diagnosis("timeout", mode="tool_execution_failure")

        result = outcome([unrelated], preference=pref)

        self.assertEqual(result["hypothesis_result"]["verdict"], "not_supported")
        self.assertEqual(result["hypothesis_result"]["supporting_case_count"], 0)
        self.assertEqual(
            result["hypothesis_result"]["alternative_failure_case_count"], 1
        )

    def test_insufficient_evidence_has_recovery_actions(self) -> None:
        result = outcome(
            [],
            preference=self.hypothesis_preference(3),
            rows=[],
            selected=[],
        )
        self.assertEqual(result["diagnosis_status"], "insufficient_evidence")
        self.assertEqual(
            result["hypothesis_result"]["verdict"], "insufficient_evidence"
        )
        self.assertTrue(result["recovery_actions"])
        self.assertTrue(
            {"code", "title", "instruction"} <= set(result["recovery_actions"][0])
        )

    def test_scope_exposes_actual_coverage(self) -> None:
        items = [diagnosis("a"), diagnosis("b")]
        pref = CasePreference(case_limit=3, max_sessions=8)
        result = outcome(
            items,
            preference=pref,
            rows=[item.session for item in items] + [row("c"), row("d")],
            selected=items[:1],
            selection_report={"judge_lookup": {"judged_session_count": 2}},
        )
        scope = result["diagnosis_scope"]
        self.assertEqual(scope["discovered_session_count"], 4)
        self.assertEqual(scope["analyzed_session_count"], 2)
        self.assertEqual(scope["selected_case_count"], 1)
        self.assertEqual(scope["requested_case_count"], 3)
        self.assertEqual(scope["coverage_ratio"], 0.5)

    def test_priority_uses_quality_not_only_frequency(self) -> None:
        diagnoses = [
            diagnosis(
                "frequent-low-1",
                mode="low_value",
                confidence=0.1,
                optimization_value="low",
                controllability="low",
            ),
            diagnosis(
                "frequent-low-2",
                mode="low_value",
                confidence=0.1,
                optimization_value="low",
                controllability="low",
            ),
            diagnosis(
                "high-value",
                mode="tool_parameter_error",
                confidence=1.0,
                optimization_value="high",
                controllability="high",
            ),
        ]
        issues = outcome(diagnoses)["prioritized_issues"]
        self.assertEqual(issues[0]["failure_mode"], "tool_parameter_error")


class DiagnosisPresentationTests(unittest.TestCase):
    def test_diagnose_emits_canonical_plan_source_without_losing_planning_fields(
        self,
    ) -> None:
        item = diagnosis("canonical")
        item.evidence = [{"source": "session", "path": "/tmp/session.jsonl"}]
        item.tool_hints = ["mcp.search"]
        pref = CasePreference(
            raw_message="优先修复工具参数问题",
            intent_text="修复工具参数问题",
            case_limit=1,
        )
        source = build_plan_source(
            task_id="EV-1",
            bot_id="bot",
            artifacts={"analysis_report_md": "/tmp/report.md"},
            layout={"workspace_root": "/tmp/workspace"},
            pref=pref,
            diags=[item],
            selection_report={"status": "ok", "diagnose_source": "local"},
        )

        self.assertEqual(source["schema_version"], "plan-source/v2")
        self.assertEqual(source["source"]["type"], "diagnose")
        self.assertEqual(source["problem"]["user_guidance"], pref.raw_message)
        self.assertEqual(source["analysis"]["case_distribution"]["total"], 1)
        self.assertEqual(
            source["planning_hints"]["case_preference"]["case_limit"], 1
        )
        self.assertEqual(source["cases"][0]["session_id"], "canonical")
        self.assertEqual(
            source["cases"][0]["analysis"]["evolution_failure_mode"],
            "tool_parameter_error",
        )
        self.assertEqual(
            source["cases"][0]["planning_hints"]["tool_hints"],
            ["mcp.search"],
        )
        self.assertEqual(
            source["extensions"]["diagnose"]["selection_report"]["status"],
            "ok",
        )

    def test_zero_case_analysis_artifact_is_a_valid_report(self) -> None:
        pref = CasePreference(case_limit=1, intent_text="只抽取1个 bad case")
        product = outcome(
            [],
            preference=pref,
            rows=[],
            selected=[],
            selection_report={"status": "underfilled", "quota_underfilled": {"bad": 1}},
        )
        with tempfile.TemporaryDirectory() as directory:
            report_path = write_analysis_report(
                Path(directory),
                "bot",
                "bot",
                [],
                {"status": "underfilled", "quota_underfilled": {"bad": 1}},
                {},
                rows=[],
                preference=pref,
                product_outcome=product,
            )
            report = report_path.read_text(encoding="utf-8")
        self.assertIn("insufficient_evidence", report)
        self.assertIn("最终选中 case：0/1", report)
        self.assertIn("无可展示的 session 证据", report)

    def test_markdown_leads_with_product_sections_and_hides_raw_report(self) -> None:
        item = diagnosis("report")
        pref = CasePreference(case_limit=1, intent_text="找主要问题")
        product = outcome([item], preference=pref)
        report = AnalysisReportBuilder(RelatedFileLocator({})).build(
            "bot",
            [item],
            {"status": "ok", "internal_debug": {"large": "payload"}},
            rows=[item.session],
            preference=pref,
            product_outcome=product,
        )
        self.assertLess(report.index("## 总体诊断"), report.index("## 技术附录"))
        self.assertIn("## 实际诊断范围", report)
        self.assertIn("## 优先问题", report)
        self.assertNotIn("selection_report：", report)
        self.assertNotIn("internal_debug", report)

    def test_report_tolerates_non_mapping_judge_metadata(self) -> None:
        item = diagnosis("malformed-judge")
        report = AnalysisReportBuilder(RelatedFileLocator({})).build(
            "bot",
            [item],
            {"status": "partial", "judge_lookup": "unavailable"},
            rows=[item.session],
            product_outcome=outcome([item]),
        )
        self.assertIn("judge stop reason：``", report)

    def test_clawweb_shape_remains_strict(self) -> None:
        item = diagnosis("shape")
        summary = outcome([item])
        case_payload = {
            "cases": [
                {
                    "case_id": "shape",
                    "case_type": "bad",
                    "evolution_failure_mode": item.evolution_failure_mode,
                    "root_cause_summary": item.root_cause_summary,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_source_path = Path(temp_dir) / "plan-source.json"
            plan_source_path.write_text(json.dumps(case_payload))
            summary["artifacts"] = {"plan_source_json": str(plan_source_path)}
            result = RunResult(
                summary_path=Path(temp_dir) / "summary.json",
                summary=summary,
            )
            output = build_execution_output(result)

        self.assertEqual(set(output), {"diagnosis", "cases"})
        self.assertEqual(set(output["diagnosis"]), {"summary", "issues"})
        self.assertEqual(
            set(output["diagnosis"]["issues"][0]),
            {"code", "title", "severity", "caseCount", "suggestion"},
        )
        self.assertEqual(
            set(output["cases"]),
            {"total", "goodCount", "badCount", "items"},
        )


if __name__ == "__main__":
    unittest.main()
