import importlib.util
import json
import tempfile
import zipfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_optimize_run", SCRIPT)
assert SPEC and SPEC.loader
handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handler)


class OptimizeBenchCompatTests(unittest.TestCase):
    def test_resolve_bench_result_prefers_workflow_result_json(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            output_dir = temp / "round-001" / "output"
            output_dir.mkdir(parents=True)
            report_path = output_dir / "nested" / "demo_benchmark_report.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(json.dumps({"tasks": [{"task_id": "t1", "grading": {"mean": 0.8}}]}), encoding="utf-8")
            workflow_result = {
                "status": "succeeded",
                "resultPath": str(report_path),
                "metrics": {"score": 0.8, "passRate": 1.0, "caseCount": 1},
            }
            (output_dir.parent / "workflow_result.json").write_text(json.dumps(workflow_result), encoding="utf-8")

            result_path, metadata = handler._resolve_bench_result_artifacts(output_dir)
            self.assertEqual(Path(result_path), report_path)
            self.assertEqual(metadata["status"], "succeeded")
            summary = handler._summary_from_workflow_result(metadata)
            self.assertEqual(summary["score"], 0.8)
            self.assertEqual(summary["pass_rate"], 1.0)
            self.assertEqual(summary["total"], 1)

    def test_restore_guard_blocks_polluted_artifact_with_old_deploy_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            artifact = temp / "legacy.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("package/skills/skills-local/demo/.git/objects/aa/.nfs123", "noise")
                archive.writestr("package/skills/skills-local/demo/SKILL.md", "valid")
            deploy_sh = temp / "deploy.sh"
            deploy_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

            with self.assertRaises(SystemExit) as caught:
                handler._assert_restore_runtime_compatible(artifact, deploy_sh)
            self.assertIn("LEGACY_ARTIFACT_REQUIRES_UPDATED_DEPLOY", str(caught.exception))

    def test_restore_guard_allows_polluted_artifact_with_compatible_deploy(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            artifact = temp / "legacy.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("package/skills/skills-local/demo/.git/objects/aa/.nfs123", "noise")
            deploy_sh = temp / "deploy.sh"
            deploy_sh.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            (temp / "deploy.py").write_text(
                "DEPLOY_NOISE_NAMES = {'.git'}\ndef _ignore_deploy_noise(directory, names): return set()\n",
                encoding="utf-8",
            )

            report = handler._assert_restore_runtime_compatible(artifact, deploy_sh)
            self.assertTrue(report["compatible"])
            self.assertTrue(report["legacy_noise_detected"])
            self.assertTrue(report["deploy_supports_legacy_noise_filter"])

    def test_restore_guard_allows_clean_artifact_with_old_deploy(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            artifact = temp / "clean.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("package/skills/skills-local/demo/SKILL.md", "valid")
            deploy_sh = temp / "deploy.sh"
            deploy_sh.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            report = handler._assert_restore_runtime_compatible(artifact, deploy_sh)
            self.assertTrue(report["compatible"])
            self.assertFalse(report["legacy_noise_detected"])

    def test_adapter_fixture_identity_uses_actual_clawbench_input_path(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            baseline_input = temp / "baseline-input"
            candidate_input = temp / "candidate-input"
            baseline_input.mkdir(); candidate_input.mkdir()
            content = "---\nid: task_same\n---\nbody\n"
            (baseline_input / "task_same.md").write_text(content, encoding="utf-8")
            (candidate_input / "task_same.md").write_text(content, encoding="utf-8")

            baseline_fixture = handler._tree_identity(baseline_input)
            state = {"identity": {"validation_fixture": {"path": "/empty", "sha256": "wrong"}}}
            fixture = handler._record_adapter_fixture_identity(
                state, "validation", {"inputPath": str(candidate_input)}
            )

            self.assertEqual(fixture["sha256"], baseline_fixture["sha256"])
            self.assertEqual(state["identity"]["validation_fixture"]["sha256"], baseline_fixture["sha256"])
            self.assertEqual(
                state["identity"]["validation_fixture_source"],
                "clawbench.workflow_result.inputPath",
            )

    def test_adapter_fixture_identity_keeps_existing_identity_when_input_missing(self):
        state = {"identity": {"validation_fixture": {"sha256": "existing"}}}
        fixture = handler._record_adapter_fixture_identity(state, "validation", {})
        self.assertEqual(fixture, {})
        self.assertEqual(state["identity"]["validation_fixture"]["sha256"], "existing")

    def test_failure_profile_surfaces_semantic_delivery_and_scorer_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            report = temp / "baseline_report.json"
            report.write_text(json.dumps({
                "tasks": [{
                    "task_id": "task_legal_01",
                    "grading": {"runs": [{
                        "score": 0.55,
                        "breakdown": {
                            "automated.expert_refund_rule_missing": 1.0,
                            "llm_judge.风险识别准确度": 0.25,
                            "llm_judge.输出格式合规": 0.5,
                            "automated.confirm_present": 0.0
                        },
                        "notes": "遗漏核心退款规则，且最终四部分输出被截断。"
                    }]}
                }]
            }, ensure_ascii=False), encoding="utf-8")
            round_dir = temp / "run" / "optimize" / "output" / "round-001"
            round_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "baseline_optimization": {"result_path": str(report)}
            }), encoding="utf-8")

            class Args:
                workspace = str(temp)
                task_id = "EV-test"
                round = 1

            original = handler.resolve_paths
            handler.resolve_paths = lambda _args: {"round_dir": round_dir}
            try:
                profile = handler._optimization_failure_profile_text(Args())
            finally:
                handler.resolve_paths = original

            self.assertIn("semantic_accuracy_cases: 1", profile)
            self.assertIn("delivery_reliability_cases: 1", profile)
            self.assertIn("scorer_conflict_cases: 1", profile)
            self.assertIn("do not optimize to token presence", profile)
            self.assertIn("遗漏核心退款规则", profile)

    def test_failure_profile_prioritizes_paired_classification_over_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            report = temp / "baseline_report.json"
            report.write_text(json.dumps({
                "tasks": [{
                    "task_id": "task_fund_01",
                    "grading": {"runs": [{
                        "score": 0.5,
                        "breakdown": {
                            "tool_called_correctly": 1.0,
                            "bizid_correct": 1.0,
                            "external_requirement_type_correct": 0.0,
                            "requirement_type_correct": 0.0,
                            "file_path_correct": 1.0,
                            "key_fields_accuracy": 0.0,
                        }
                    }]}
                }]
            }, ensure_ascii=False), encoding="utf-8")
            round_dir = temp / "run/optimize/output/round-001"
            input_dir = temp / "run/optimize/input"
            round_dir.mkdir(parents=True)
            input_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "baseline_optimization": {"result_path": str(report)}
            }), encoding="utf-8")
            (input_dir / "objective.json").write_text(json.dumps({
                "optimization_baseline_score": 0.5,
                "test_baseline_score": 0.45,
            }), encoding="utf-8")

            original = handler.resolve_paths
            handler.resolve_paths = lambda _args: {"round_dir": round_dir, "optimize_input_dir": input_dir}
            try:
                profile = handler._optimization_failure_profile_text(object())
            finally:
                handler.resolve_paths = original

            self.assertIn("paired_classification_failures: 1", profile)
            self.assertIn("semantic_only_failures: 1", profile)
            self.assertIn("requirementType and externalRequirementType", profile)
            self.assertIn("do not spend the round on output format", profile)
            self.assertLess(profile.index("key_fields_accuracy"), profile.index("tool_called_correctly") if "tool_called_correctly" in profile else len(profile))

    def test_failure_profile_flags_underexposed_generalization_gap_without_validation_details(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            report = temp / "baseline_report.json"
            report.write_text(json.dumps({
                "tasks": [{"task_id": "train_only", "grading": {"runs": [{
                    "score": 1.0,
                    "breakdown": {"key_fields_accuracy": 1.0, "tool_called_correctly": 1.0}
                }]}}]
            }), encoding="utf-8")
            round_dir = temp / "run/optimize/output/round-001"
            input_dir = temp / "run/optimize/input"
            round_dir.mkdir(parents=True)
            input_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "baseline_optimization": {"result_path": str(report)}
            }), encoding="utf-8")
            (input_dir / "objective.json").write_text(json.dumps({
                "optimization_baseline_score": 1.0,
                "test_baseline_score": 0.5,
            }), encoding="utf-8")

            original = handler.resolve_paths
            handler.resolve_paths = lambda _args: {"round_dir": round_dir, "optimize_input_dir": input_dir}
            try:
                profile = handler._optimization_failure_profile_text(object())
            finally:
                handler.resolve_paths = original

            self.assertIn("aggregate_generalization_gap: 0.5", profile)
            self.assertIn("optimization_underexposed: True", profile)
            self.assertIn("COVERAGE RISK", profile)
            self.assertIn("A no-op is not evidence-based", profile)
            self.assertNotIn("task_99", profile)

    def test_scene_playbook_routes_paired_taxonomy_to_authoritative_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            report = temp / "baseline_report.json"
            report.write_text(json.dumps({
                "tasks": [{"task_id": "task_fund_train", "grading": {"runs": [{
                    "score": 0.5,
                    "breakdown": {
                        "tool_called_correctly": 1.0,
                        "bizid_correct": 1.0,
                        "file_path_correct": 1.0,
                        "requirement_type_correct": 0.0,
                        "external_requirement_type_correct": 0.0,
                        "key_fields_accuracy": 0.0,
                    }
                }]}}]
            }, ensure_ascii=False), encoding="utf-8")
            round_dir = temp / "run/optimize/output/round-001"
            round_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "baseline_optimization": {"result_path": str(report)}
            }), encoding="utf-8")

            original = handler.resolve_paths
            handler.resolve_paths = lambda _args: {"round_dir": round_dir}
            try:
                playbook = handler._optimization_scene_playbook_text(object())
            finally:
                handler.resolve_paths = original

            self.assertIn("scene_family: paired_business_taxonomy", playbook)
            self.assertIn("both_axes_low: 1", playbook)
            self.assertIn("earliest runtime prompt or reconciliation function", playbook)
            self.assertIn("over adding a downstream SKILL checklist", playbook)
            self.assertIn("exact canonical enum labels", playbook)
            self.assertNotIn("task_fund_train", playbook)

    def test_scene_playbook_routes_legal_process_ceiling_to_risk_atoms_and_delivery_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            report = temp / "baseline_report.json"
            report.write_text(json.dumps({
                "tasks": [{"task_id": "task_legal_train", "grading": {"runs": [{
                    "score": 0.6,
                    "breakdown": {
                        "automated": {
                            "expert_refund_rule_missing": 0.0,
                            "confirm_present": 1.0,
                            "v5_frameworks_read": 1.0,
                            "v5_validators_run": 1.0,
                            "v5_repair_pass": 1.0,
                            "v5_dual_verification": 1.0,
                            "yuque_doc_read": 1.0,
                            "evidence_based": 1.0,
                        },
                        "llm_judge": {
                            "风险识别准确度（含原文证据）": 0.25,
                            "输出格式合规": 0.5,
                            "确认动作": 1.0,
                            "v5流程与验证器门控": 1.0,
                        },
                    },
                    "notes": "核心风险遗漏，且对话输出被截断，缺失【其他审查项说明】。"
                }]}}]
            }, ensure_ascii=False), encoding="utf-8")
            round_dir = temp / "run/optimize/output/round-001"
            round_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "baseline_optimization": {"result_path": str(report)}
            }), encoding="utf-8")

            original = handler.resolve_paths
            handler.resolve_paths = lambda _args: {"round_dir": round_dir}
            try:
                playbook = handler._optimization_scene_playbook_text(object())
                profile = handler._optimization_failure_profile_text(object())
            finally:
                handler.resolve_paths = original

            self.assertIn("scene_family: legal_risk_review", playbook)
            self.assertIn("process_clean_but_semantic_low: 1", playbook)
            self.assertIn("truncation_note_cases: 1", playbook)
            self.assertIn("risk-atom contract", playbook)
            self.assertIn("replace or merge verbose stage narration", playbook)
            self.assertIn("do not add another stage, validator", playbook)
            self.assertIn("automated.expert_refund_rule_missing", profile)
            self.assertIn("llm_judge.风险识别准确度", profile)

    def test_resolve_bench_result_falls_back_to_report_glob(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            output_dir = temp / "output"
            output_dir.mkdir(parents=True)
            report_path = output_dir / "run" / "demo_benchmark_report.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(json.dumps({"tasks": [{"task_id": "t1", "grading": {"mean": 0.5}}]}), encoding="utf-8")

            result_path, metadata = handler._resolve_bench_result_artifacts(output_dir)
            self.assertEqual(Path(result_path), report_path)
            self.assertEqual(metadata, {})
            summary = handler._compute_bench_summary(Path(result_path))
            self.assertAlmostEqual(summary["score"], 0.5)

    def test_bench_report_parsers_ignore_null_task_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "governance_benchmark_report.json"
            report_path.write_text(json.dumps({
                "tasks": [
                    None,
                    {"task_id": "t1", "status": "succeeded", "grading": {"mean": 0.75}},
                ],
            }), encoding="utf-8")

            summary = handler._compute_bench_summary(report_path)
            scores = handler._task_scores_from_report(report_path)

            self.assertAlmostEqual(summary["score"], 0.75)
            self.assertEqual(summary["total"], 2)
            self.assertEqual(scores, {"t1": 0.75})

            report_path.write_text("null", encoding="utf-8")
            self.assertEqual(handler._task_scores_from_report(report_path), {})


if __name__ == "__main__":
    unittest.main()

class BenchOnlyAcceptanceTests(unittest.TestCase):
    def _run_accept(self, *, baseline, candidate, train_baseline=None, train_candidate=None, extra_state=None):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            round_dir = temp / "run" / "optimize" / "output" / "round-001"
            run_dir = temp / "run"
            round_dir.mkdir(parents=True)
            manifest = run_dir / "optimize" / "output" / "optimize_manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({
                "schema_version": "evolution.run_manifest.v0",
                "last_accepted_round": 0,
                "last_accepted_validation_score": baseline,
                "accepted_baseline_optimization": {
                    "summary": {"score": train_baseline}
                } if train_baseline is not None else {},
                "rounds": [],
            }), encoding="utf-8")
            state = {
                "bench": {
                    "validation": {"summary": {"score": candidate}},
                    "optimization": {"summary": {"score": train_candidate}},
                },
                "change_summary": {"is_noop": False, "changed_files": ["skills/demo/SKILL.md"]},
                "steps": {},
            }
            state.update(extra_state or {})
            (round_dir / "round_state.json").write_text(json.dumps(state), encoding="utf-8")

            class Args:
                round = 1
                task_id = "EV-test"

            original = handler.resolve_paths
            handler.resolve_paths = lambda _args: {
                "round_dir": round_dir,
                "run_dir": run_dir,
                "task_id": "EV-test",
                "spec_dir": round_dir / "spec",
                "accept_dir": round_dir / "acceptance",
            }
            try:
                handler.action_accept(Args())
                report = json.loads((round_dir / "acceptance" / "acceptance_report.json").read_text())
                saved_state = json.loads((round_dir / "round_state.json").read_text())
            finally:
                handler.resolve_paths = original
            return report, saved_state

    def test_higher_test_score_passes_even_when_legacy_gate_is_invalid(self):
        report, state = self._run_accept(
            baseline=0.90,
            candidate=0.91,
            train_baseline=0.80,
            train_candidate=0.70,
            extra_state={
                "candidate_gate": {"valid": False, "reasons": ["legacy gate missing binding"]},
                "candidate_opt_gate": {"valid": False, "decision": "invalid_signal_plan"},
                "full_opt_gate": {"valid": False, "hard_protected_regressions": [{"task_id": "x"}]},
            },
        )
        self.assertEqual(report["bench_decision"], "passed")
        self.assertFalse(report["accepted"])
        self.assertEqual(report["promotion_status"], "pending")
        self.assertAlmostEqual(report["score"]["delta"], 0.01)
        self.assertEqual(report["train"]["role"], "informational")
        self.assertEqual(state["bench_decision"], "passed")
        self.assertFalse(state["accepted"])
        self.assertEqual(state["promotion_status"], "pending")

    def test_equal_or_lower_test_score_is_not_improved(self):
        for candidate in (0.90, 0.89):
            report, _ = self._run_accept(baseline=0.90, candidate=candidate)
            self.assertEqual(report["bench_decision"], "not_improved")
            self.assertFalse(report["accepted"])

    def test_missing_scores_have_explicit_bench_outcomes(self):
        report, _ = self._run_accept(baseline=None, candidate=0.91)
        self.assertEqual(report["bench_decision"], "baseline_unavailable")
        self.assertFalse(report["accepted"])
        report, _ = self._run_accept(baseline=0.90, candidate=None)
        self.assertEqual(report["bench_decision"], "bench_failed")
        self.assertFalse(report["accepted"])

    def test_review_normalization_discards_retired_fields_and_uses_bench_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            accept_dir = temp / "accept"
            spec_dir = temp / "spec"
            round_dir = temp / "round"
            output_dir = temp / "output"
            accept_dir.mkdir(); spec_dir.mkdir(); round_dir.mkdir(); output_dir.mkdir()
            (accept_dir / "acceptance_report.json").write_text(json.dumps({
                "accepted": False, "decision": "passed", "bench_decision": "passed",
                "promotion_status": "pending",
            }), encoding="utf-8")
            paths = {
                "accept_dir": accept_dir, "spec_dir": spec_dir, "round_dir": round_dir,
                "run_dir": temp, "optimize_output_dir": output_dir,
            }
            normalized, warnings = handler._normalize_review_decision(paths, 2, {
                "schema_version": "evolution.review_decision.v2",
                "acceptance_decision": "not_improved",
                "summary": "Bench improved.",
                "confidence": "high",
                "protected_behaviors": ["legacy"],
                "protected_signals": [{"id": "legacy"}],
                "evaluation_contract": {"required": True},
                "hypotheses": [],
                "direction_decisions": [],
            })
            self.assertEqual(normalized["acceptance_decision"], "passed")
            self.assertNotIn("protected_behaviors", normalized)
            self.assertNotIn("protected_signals", normalized)
            self.assertNotIn("evaluation_contract", normalized)
            self.assertTrue(any("retired review field" in warning for warning in warnings))
            self.assertTrue(any("authoritative Bench decision" in warning for warning in warnings))

    def test_new_spec_markdown_omits_retired_empty_sections(self):
        rendered = handler._render_spec_v1_markdown({
            "spec_version": "v2", "parent_spec_version": "v1",
            "objective_contract": {"objective_summary": ["improve"]},
            "accepted_baseline_snapshot": {}, "experiment_questions": [],
            "search_contract": {}, "scope_contract": {},
            "history_ref": "history", "failure_registry_ref": "failures",
            "mutation_operator_library_ref": "operators",
        })
        self.assertNotIn("Protected Behaviors", rendered)
        self.assertNotIn("Evaluation Contract", rendered)

    def test_review_payload_does_not_expose_legacy_gates(self):
        payload = handler._acceptance_for_review({
            "bench_decision": "passed",
            "accepted": True,
            "score": {"baseline": 0.9, "candidate": 0.95, "delta": 0.05},
            "train": {"baseline": 0.8, "candidate": 0.81, "delta": 0.01},
            "candidate_gate": {"valid": False},
            "candidate_opt_gate": {"valid": False},
            "full_opt_gate": {"valid": False},
            "protected_signals": [{"id": "secret"}],
        })
        self.assertEqual(payload["bench_decision"], "passed")
        self.assertNotIn("candidate_gate", payload)
        self.assertNotIn("candidate_opt_gate", payload)
        self.assertNotIn("full_opt_gate", payload)
        self.assertNotIn("protected_signals", payload)
