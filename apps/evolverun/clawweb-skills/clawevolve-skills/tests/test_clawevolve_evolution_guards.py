import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock
from types import SimpleNamespace


PATH = Path(__file__).parents[1] / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("optimize_evolution_guards", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def write_report(path: Path, scores: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "model": "antchat/GLM-5",
        "benchmark_version": "1.2.1",
        "tasks": [
            {"task_id": task_id, "status": "success", "grading": {"runs": [{"score": score}]}}
            for task_id, score in scores.items()
        ],
    }), encoding="utf-8")


def valid_manifest(target_file="skills/demo/SKILL.md"):
    common = {"failure_signature": "f", "suspected_root_cause": "r", "alternative_causes": ["a"], "proposed_change": "c"}
    return {
        "_source_path": "/tmp/change_manifest.json", "selected_proposal_id": "P1",
        "spec_hypothesis_assessment": {"decision": "accept", "reason": "evidence", "unresolved_alternatives": []},
        "expected_signals": [{"task_id": "task_a", "metric": "score", "direction": "increase", "min_delta": 0.1}],
        "protected_signals": [{"task_id": "task_b", "metric": "score", "direction": "maintain", "max_drop": 0.1}],
        "proposals": [
            {**common, "proposal_id": "P1", "selected_operator": "SHORTEN_CRITICAL_PATH", "decision": "selected", "complexity": "low", "impact_radius": "one"},
            {**common, "proposal_id": "P2", "selected_operator": "ADD_RESULT_VERIFIER", "decision": "deferred"},
            {**common, "proposal_id": "P3", "selected_operator": "SEPARATE_ANALYSIS_FROM_RENDERING", "decision": "rejected"},
        ],
        "edits": [{"edit_id": "E1", "proposal_id": "P1", "target_file": target_file, "failure_signature": "f", "suspected_root_cause": "r", "alternative_causes": ["a"], "selected_operator": "SHORTEN_CRITICAL_PATH", "falsifiable_prediction": "p", "rollback_condition": "r", "local_check": "c"}],
    }


class EvolutionGuardTests(unittest.TestCase):
    def test_bootstrap_validation_baseline_reads_pre_opt_test_report(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = Path(root) / "clawevolve_results" / "EV-1"
            report = run_dir / "bench/baseline/STEP-1/test/output/demo_benchmark_report.json"
            write_report(report, {"task_a": 0.8, "task_b": 1.0})
            (run_dir / "bench").mkdir(exist_ok=True)
            (run_dir / "bench/bootstrap_manifest.json").write_text(json.dumps({
                "test_summary": {"overall_score": 0.9},
                "test_identity": {
                    "bench_model": "antchat/GLM-5",
                    "benchmark_version": "1.2.1",
                    "bench_mode": "adapter",
                    "suite": "all",
                    "validation_fixture": {"sha256": "fixture-a"},
                },
            }), encoding="utf-8")

            baseline = MOD._bootstrap_validation_baseline({"run_dir": run_dir})

            self.assertTrue(baseline["available"])
            self.assertEqual(baseline["round_id"], 0)
            self.assertEqual(baseline["validation_score"], 0.9)
            self.assertEqual(baseline["validation_task_scores"], {"task_a": 0.8, "task_b": 1.0})
            self.assertEqual(baseline["identity"]["bench_model"], "antchat/GLM-5")
            self.assertTrue(baseline["identity_complete"])

    def test_business_candidate_scope_blocks_engine_and_history(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {"tune_dir": Path(root) / "tune"}
            args = Namespace(allow_engine_mutation=False)
            report = MOD._candidate_scope_report(args, paths, {
                "touched_paths": [
                    "skills/skills-local/activity-review/SKILL.md",
                    "skills/skills-local/clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py",
                    "clawevolve-skills/clawevolve-workflow/SKILL.md",
                    "clawevolve_results/EV-1/round_state.json",
                ]
            })

            self.assertFalse(report["valid"])
            categories = {item["category"] for item in report["blocked"]}
            self.assertIn("engine_self_mutation", categories)
            self.assertIn("evidence_or_history", categories)
            allowed = {item["path"] for item in report["checked"] if item["allowed"]}
            self.assertIn("skills/skills-local/activity-review/SKILL.md", allowed)

    def test_release_script_resolution_does_not_fall_back_to_legacy_skill_roots(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            private_root = base / "clawevolve-skills"
            legacy_script = base / "skills/skills-local/clawevolve-pack/scripts/pack.sh"
            legacy_script.parent.mkdir(parents=True)
            legacy_script.write_text("#!/bin/sh\n", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                MOD.find_script(str(private_root), "clawevolve-pack", "scripts/pack.sh")

            private_script = private_root / "clawevolve-pack/scripts/pack.sh"
            private_script.parent.mkdir(parents=True)
            private_script.write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual(
                MOD.find_script(str(private_root), "clawevolve-pack", "scripts/pack.sh"),
                private_script.resolve(),
            )

    def test_review_leak_check_allows_optimization_task_and_rejects_validation_task(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            spec_dir = base / "spec"
            round_dir = base / "round-002"
            spec_dir.mkdir()
            round_dir.mkdir()
            opt_report = base / "opt.json"
            val_report = base / "val.json"
            write_report(opt_report, {"task_02_train": 0.7})
            write_report(val_report, {"task_15_hidden": 0.8})
            (round_dir / "round_state.json").write_text(json.dumps({
                "bench": {
                    "optimization": {"resultPath": str(opt_report)},
                    "validation": {"resultPath": str(val_report)},
                }
            }), encoding="utf-8")
            (spec_dir / "spec-v2.md").write_text(
                "optimization task_02_train improved; do not target task_15_hidden",
                encoding="utf-8",
            )
            (spec_dir / "spec_update_report.md").write_text(
                "validation leaked expert_hidden_field",
                encoding="utf-8",
            )

            report = MOD._review_output_leak_report({"spec_dir": spec_dir, "round_dir": round_dir}, 2)

            self.assertFalse(report["valid"])
            kinds = {x["kind"] for x in report["findings"]}
            self.assertIn("validation or unknown benchmark task id", kinds)
            self.assertIn("validation rubric/expert field", kinds)
            matches = {match for finding in report["findings"] for match in finding["matches"]}
            self.assertNotIn("task_02_train", matches)
            self.assertIn("task_15_hidden", matches)

    def test_acceptance_injected_into_review_withholds_case_pairs(self):
        safe = MOD._acceptance_for_review({
            "decision": "rejected_paired",
            "paired_eval": {"n": 2, "pairs": [{"task_id": "task_01", "delta": -0.1}]},
        })
        self.assertNotIn("paired_eval", safe)
        self.assertNotIn("change_manifest", safe)

    def test_invalid_candidate_short_circuits_validation(self):
        with tempfile.TemporaryDirectory() as root:
            round_dir = Path(root) / "round-002"
            tune_dir = round_dir / "tune"
            tune_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text("{}", encoding="utf-8")
            paths = {"round_dir": round_dir, "tune_dir": tune_dir}
            args = Namespace(round=2, bench_mode="local", staged_bench=True, enforce_candidate_gate=True)
            summary = {"is_noop": False, "touched_paths": ["skills/skills-local/clawevolve-workflow/SKILL.md"]}
            gate = {"valid": False, "is_noop": False, "reasons": ["engine self mutation"]}

            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_write_round_change_summary", return_value=summary), \
                 mock.patch.object(MOD, "_candidate_prevalidation_gate", return_value=gate), \
                 mock.patch.object(MOD, "action_bench_local") as bench_mock, \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), \
                 mock.patch.object(MOD, "_print_json"):
                MOD.action_bench_val(args)

            bench_mock.assert_called_once()

    def test_round_one_compares_against_bootstrap_instead_of_auto_accepting(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            run_dir = workspace / "clawevolve_results/EV-1"
            round_dir = run_dir / "optimize/output/round-001"
            tune_dir = round_dir / "tune"
            accept_dir = round_dir / "acceptance"
            tune_dir.mkdir(parents=True)
            accept_dir.mkdir()

            baseline_report = run_dir / "bench/baseline/STEP-1/test/output/baseline_benchmark_report.json"
            current_report = round_dir / "bench/validation/current_benchmark_report.json"
            write_report(baseline_report, {"task_a": 0.9, "task_b": 0.9})
            write_report(current_report, {"task_a": 0.8, "task_b": 0.8})
            identity = {
                "bench_model": "antchat/GLM-5",
                "benchmark_version": "1.2.1",
                "bench_mode": "adapter",
                "suite": "all",
                "validation_fixture": {"sha256": "fixture-a"},
            }
            (run_dir / "bench/bootstrap_manifest.json").write_text(json.dumps({
                "test_summary": {"overall_score": 0.9},
                "test_identity": identity,
            }), encoding="utf-8")

            state = {
                "identity": identity,
                "bench": {
                    "optimization": {"summary": {"score": 0.7}},
                    "validation": {"resultPath": str(current_report), "summary": {"score": 0.8}},
                },
                "change_summary": {"is_noop": False, "changed_files": [], "touched_paths": ["skills/skills-local/activity-review/SKILL.md"]},
                "reachability": {"activated": True},
                "change_manifest": {"changes": []},
                "skill_creation": {"valid": True},
                "candidate_gate": {"valid": True, "is_noop": False, "reasons": []},
                "candidate_opt_gate": {"valid": True, "effect_gate_passed": True, "protected_gate_passed": True, "reasons": []},
                "full_opt_gate": {"valid": True, "major_regressions": []},
                "full_opt_gate": {"valid": True, "major_regressions": []},
            }
            (round_dir / "round_state.json").write_text(json.dumps(state), encoding="utf-8")
            paths = {
                "workspace": str(workspace), "run_dir": run_dir, "round_dir": round_dir,
                "tune_dir": tune_dir, "accept_dir": accept_dir, "task_id": "EV-1",
            }
            args = Namespace(round=1, acceptance_margin=0.0, paired_min_runs=1,
                             paired_min_win_rate=0.5, exploration_loss_budget=0.0)

            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_write_evidence_database"), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), \
                 mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)

            acceptance = json.loads((accept_dir / "acceptance_report.json").read_text())
            self.assertFalse(acceptance["accepted"])
            self.assertEqual(acceptance["baseline"]["round_id"], 0)
            self.assertEqual(acceptance["baseline"]["validation_score"], 0.9)
            self.assertEqual(acceptance["decision"], "not_improved")
            self.assertEqual(acceptance["promotion_status"], "not_started")


    def test_rejected_candidate_never_triggers_stop(self):
        self.assertEqual(
            MOD._should_stop_after_round(accepted=False, validation_score=0.99, stop_score=0.9),
            (False, "candidate was not accepted"),
        )
        self.assertTrue(MOD._should_stop_after_round(accepted=True, validation_score=0.91, stop_score=0.9)[0])

    def test_objective_score_target_drives_automatic_stop(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir = Path(root) / "optimize/input"
            input_dir.mkdir(parents=True)
            (input_dir / "objective.json").write_text(json.dumps({
                "primary_metric": {
                    "name": "task_success_rate", "display_name": "任务成功率",
                    "operator": ">=", "target": 0.92, "unit": "ratio",
                },
            }), encoding="utf-8")
            criterion = MOD._objective_completion_criterion(
                Namespace(), {"optimize_input_dir": input_dir},
            )

            self.assertEqual(criterion["source"], "objective_primary_metric")
            self.assertEqual(criterion["target_score"], 0.92)
            self.assertEqual(criterion["primary_metric"]["name"], "task_success_rate")
            self.assertTrue(criterion["automatic_stop"])
            self.assertTrue(MOD._should_stop_for_objective(
                accepted=True, validation_score=0.92, criterion=criterion,
            )[0])
            self.assertFalse(MOD._should_stop_for_objective(
                accepted=True, validation_score=0.91, criterion=criterion,
            )[0])

    def test_objective_without_primary_metric_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir = Path(root) / "optimize/input"
            input_dir.mkdir(parents=True)
            (input_dir / "objective.json").write_text(json.dumps({
                "objective_text": "测试集得分不低于 0.95",
            }), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "primary_metric is required"):
                MOD._objective_completion_criterion(
                    Namespace(), {"optimize_input_dir": input_dir},
                )

    def test_invalid_primary_metric_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir = Path(root) / "optimize/input"
            input_dir.mkdir(parents=True)
            (input_dir / "objective.json").write_text(json.dumps({
                "primary_metric": {
                    "name": "task_success_rate", "operator": ">=", "target": 1.2,
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "primary_metric.target"):
                MOD._objective_completion_criterion(
                    Namespace(), {"optimize_input_dir": input_dir},
                )

    def test_acceptance_report_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {"accept_dir": Path(root)}
            with self.assertRaises(SystemExit):
                MOD._require_acceptance_report(paths)
            (Path(root) / "acceptance_report.json").write_text(json.dumps({"decision": "accepted"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                MOD._require_acceptance_report(paths)

    def test_mixed_reachable_and_unknown_paths_fail_candidate_gate(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            workspace = base / "workspace"
            skill = workspace / "skills/skills-local/demo/SKILL.md"
            extra = workspace / "misc/side_effect.py"
            skill.parent.mkdir(parents=True)
            extra.parent.mkdir(parents=True)
            skill.write_text("demo", encoding="utf-8")
            extra.write_text("print('x')", encoding="utf-8")
            tune_dir = base / "tune"
            round_dir = base / "round"
            tune_dir.mkdir()
            round_dir.mkdir()
            (round_dir / "round_state.json").write_text("{}", encoding="utf-8")
            summary = {
                "trusted": True,
                "is_noop": False,
                "touched_paths": ["skills/skills-local/demo/SKILL.md", "misc/side_effect.py"],
                "changed_files": [
                    {"path": "skills/skills-local/demo/SKILL.md", "change": "modified"},
                    {"path": "misc/side_effect.py", "change": "modified"},
                ],
                "agent_report_consistency": {"matches": True},
            }
            gate = MOD._candidate_prevalidation_gate(
                Namespace(allow_engine_mutation=False),
                {"workspace": str(workspace), "tune_dir": tune_dir, "round_dir": round_dir},
                summary,
            )
            self.assertFalse(gate["valid"])
            self.assertFalse(gate["reachability"]["activated"])

    def test_workspace_manifest_ignores_non_runtime_skills_pool_churn(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            workspace = base / "workspace"
            target = workspace / "skills/skills-local/demo/SKILL.md"
            noise = workspace / "skills-pool/skills-repo/demo/.atomic.tmp"
            target.parent.mkdir(parents=True)
            noise.parent.mkdir(parents=True)
            target.write_text("before", encoding="utf-8")
            noise.write_text("before", encoding="utf-8")
            tune_dir = base / "tune"
            tune_dir.mkdir()
            paths = {"workspace": str(workspace), "tune_dir": tune_dir}
            MOD._capture_workspace_manifest(paths, "before")
            target.write_text("after", encoding="utf-8")
            noise.write_text("background churn", encoding="utf-8")
            (tune_dir / "changed_files.txt").write_text("skills/skills-local/demo/SKILL.md\n", encoding="utf-8")
            (tune_dir / "diff.patch").write_text(
                "diff --git a/skills/skills-local/demo/SKILL.md b/skills/skills-local/demo/SKILL.md\n"
                "--- a/skills/skills-local/demo/SKILL.md\n+++ b/skills/skills-local/demo/SKILL.md\n"
                "@@ -1 +1 @@\n-before\n+after\n",
                encoding="utf-8",
            )
            summary = MOD._round_change_summary(paths)
            self.assertEqual(summary["touched_paths"], ["skills/skills-local/demo/SKILL.md"])
            self.assertTrue(summary["agent_report_consistency"]["matches"])

    def test_system_workspace_diff_detects_unreported_change(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            workspace = base / "workspace"
            target = workspace / "skills/skills-local/demo/SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("before", encoding="utf-8")
            tune_dir = base / "tune"
            tune_dir.mkdir()
            paths = {"workspace": str(workspace), "tune_dir": tune_dir}
            MOD._capture_workspace_manifest(paths, "before")
            target.write_text("after", encoding="utf-8")
            (tune_dir / "changed_files.txt").write_text("# omitted\n", encoding="utf-8")
            (tune_dir / "diff.patch").write_text("", encoding="utf-8")

            summary = MOD._round_change_summary(paths)

            self.assertTrue(summary["trusted"])
            self.assertEqual(summary["touched_paths"], ["skills/skills-local/demo/SKILL.md"])
            self.assertFalse(summary["agent_report_consistency"]["matches"])
            self.assertEqual(summary["agent_report_consistency"]["unreported_files"], ["skills/skills-local/demo/SKILL.md"])

    def test_task_level_improvement_requires_replication(self):
        paired = MOD._paired_eval_from_scores({"task_a": 0.5}, {"task_a": 0.8}, 0.0, 0.5)
        paired["source"] = "baseline/current task scores"
        self.assertTrue(paired["passed"])
        self.assertFalse(MOD._paired_eval_has_replicates(paired, 1))

    def test_action_accept_does_not_promote_single_sample_improvement(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            run_dir = workspace / "clawevolve_results/EV-2"
            round_dir = run_dir / "optimize/output/round-001"
            tune_dir = round_dir / "tune"
            accept_dir = round_dir / "acceptance"
            tune_dir.mkdir(parents=True)
            accept_dir.mkdir()
            baseline_report = run_dir / "bench/baseline/STEP-1/test/output/baseline_benchmark_report.json"
            current_report = round_dir / "bench/validation/current_benchmark_report.json"
            write_report(baseline_report, {"task_a": 0.7, "task_b": 0.7})
            write_report(current_report, {"task_a": 0.9, "task_b": 0.9})
            identity = {
                "bench_model": "antchat/GLM-5", "benchmark_version": "1.2.1",
                "bench_mode": "adapter", "suite": "all",
                "validation_fixture": {"sha256": "fixture-a"},
            }
            (run_dir / "bench/bootstrap_manifest.json").write_text(json.dumps({
                "test_summary": {"overall_score": 0.7}, "test_identity": identity,
            }), encoding="utf-8")
            (round_dir / "round_state.json").write_text(json.dumps({
                "identity": identity,
                "bench": {"optimization": {"summary": {"score": 0.7}}, "validation": {"resultPath": str(current_report), "summary": {"score": 0.9}}},
                "change_summary": {"is_noop": False, "touched_paths": ["skills/skills-local/demo/SKILL.md"]},
                "reachability": {"activated": True}, "change_manifest": {"changes": []},
                "skill_creation": {"valid": True}, "candidate_gate": {"valid": True, "is_noop": False, "reasons": []},
                "candidate_opt_gate": {"valid": True, "effect_gate_passed": True, "protected_gate_passed": True, "reasons": []},
                "full_opt_gate": {"valid": True, "major_regressions": []},
                "full_opt_gate": {"valid": True, "major_regressions": []},
            }), encoding="utf-8")
            paths = {"workspace": str(workspace), "run_dir": run_dir, "round_dir": round_dir, "tune_dir": tune_dir, "accept_dir": accept_dir, "task_id": "EV-2"}
            args = Namespace(round=1, acceptance_margin=0.0, paired_min_runs=1, paired_min_win_rate=0.5, exploration_loss_budget=0.0)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((accept_dir / "acceptance_report.json").read_text())
            self.assertFalse(acceptance["accepted"])
            self.assertEqual(acceptance["decision"], "passed")
            self.assertEqual(acceptance["promotion_status"], "pending")

    def test_aggregate_single_positive_without_task_scores_needs_replication(self):
        # C1 regression: when the registry has a baseline score but no paired
        # task-level scores (paired_n == 0, aggregate_only), a single positive
        # aggregate delta must NOT directly promote to baseline. It must route to
        # needs_replication so the result is replicated before any promotion.
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            run_dir = workspace / "clawevolve_results/EV-C1"
            output = run_dir / "optimize/output"; output.mkdir(parents=True)
            round_dir = output / "round-002"; tune_dir = round_dir / "tune"; accept_dir = round_dir / "acceptance"
            tune_dir.mkdir(parents=True); accept_dir.mkdir()
            current_report = round_dir / "bench/validation/current.json"
            write_report(current_report, {"task_a": 0.9, "task_b": 0.9})
            identity = {"bench_model": "antchat/GLM-5", "benchmark_version": "1.2.1", "bench_mode": "adapter", "suite": "all", "validation_fixture": {"sha256": "fixture-a"}, "cache_complete": True}
            # Registry has baseline score + complete identity but NO task-level scores,
            # so _load_or_compute_paired_eval returns aggregate_only n==0.
            (output / "optimize_manifest.json").write_text(json.dumps({
                "schema_version": "evolution.run_manifest.v0", "task_id": "EV-C1", "evolve_run_id": "EV-C1", "rounds": [],
                "last_accepted_round": 1, "last_accepted_validation_score": 0.7,
                "last_accepted_identity": identity, "last_accepted_identity_complete": True}))
            (round_dir / "round_state.json").write_text(json.dumps({
                "identity": identity,
                "bench": {"optimization": {"summary": {"score": 0.7}}, "validation": {"resultPath": str(current_report), "summary": {"score": 0.9}}},
                "change_summary": {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/skills-local/demo/SKILL.md"]},
                "reachability": {"activated": True}, "change_manifest": {"changes": []}, "skill_creation": {"valid": True},
                "candidate_gate": {"valid": True, "is_noop": False, "reasons": []},
                "candidate_opt_gate": {"valid": True, "effect_gate_passed": True, "protected_gate_passed": True, "reasons": []},
                "full_opt_gate": {"valid": True, "major_regressions": []}}))
            paths = {"workspace": str(workspace), "run_dir": run_dir, "round_dir": round_dir, "tune_dir": tune_dir, "accept_dir": accept_dir, "task_id": "EV-C1", "optimize_output_dir": output}
            args = Namespace(round=2, acceptance_margin=0.0, replication_band=0.03, paired_min_runs=1, paired_min_win_rate=0.5, exploration_loss_budget=0.0)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((accept_dir / "acceptance_report.json").read_text())
            self.assertFalse(acceptance["accepted"])
            self.assertEqual(acceptance["decision"], "passed")
            self.assertEqual(acceptance["promotion_status"], "pending")
            self.assertFalse(acceptance["restore_required"])

    def test_aggregate_clearly_negative_single_sample_can_early_stop(self):
        # C1: a clearly negative single aggregate result (below margin - band) may
        # sequential-early-stop with restore rather than forcing replication.
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            run_dir = workspace / "clawevolve_results/EV-C2"
            output = run_dir / "optimize/output"; output.mkdir(parents=True)
            round_dir = output / "round-002"; tune_dir = round_dir / "tune"; accept_dir = round_dir / "acceptance"
            tune_dir.mkdir(parents=True); accept_dir.mkdir()
            current_report = round_dir / "bench/validation/current.json"
            write_report(current_report, {"task_a": 0.6, "task_b": 0.6})
            identity = {"bench_model": "antchat/GLM-5", "benchmark_version": "1.2.1", "bench_mode": "adapter", "suite": "all", "validation_fixture": {"sha256": "fixture-a"}, "cache_complete": True}
            (output / "optimize_manifest.json").write_text(json.dumps({
                "schema_version": "evolution.run_manifest.v0", "task_id": "EV-C2", "evolve_run_id": "EV-C2", "rounds": [],
                "last_accepted_round": 1, "last_accepted_validation_score": 0.7,
                "last_accepted_identity": identity, "last_accepted_identity_complete": True}))
            (round_dir / "round_state.json").write_text(json.dumps({
                "identity": identity,
                "bench": {"optimization": {"summary": {"score": 0.7}}, "validation": {"resultPath": str(current_report), "summary": {"score": 0.6}}},
                "change_summary": {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/skills-local/demo/SKILL.md"]},
                "reachability": {"activated": True}, "change_manifest": {"changes": []}, "skill_creation": {"valid": True},
                "candidate_gate": {"valid": True, "is_noop": False, "reasons": []},
                "candidate_opt_gate": {"valid": True, "effect_gate_passed": True, "protected_gate_passed": True, "reasons": []},
                "full_opt_gate": {"valid": True, "major_regressions": []}}))
            paths = {"workspace": str(workspace), "run_dir": run_dir, "round_dir": round_dir, "tune_dir": tune_dir, "accept_dir": accept_dir, "task_id": "EV-C2", "optimize_output_dir": output}
            args = Namespace(round=2, acceptance_margin=0.0, replication_band=0.03, paired_min_runs=1, paired_min_win_rate=0.5, exploration_loss_budget=0.0)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((accept_dir / "acceptance_report.json").read_text())
            self.assertFalse(acceptance["accepted"])
            self.assertEqual(acceptance["decision"], "not_improved")
            self.assertTrue(acceptance["restore_required"])

    def test_bootstrap_model_mismatch_is_incomparable(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            run_dir = workspace / "clawevolve_results/EV-3"
            round_dir = run_dir / "optimize/output/round-001"
            tune_dir = round_dir / "tune"
            accept_dir = round_dir / "acceptance"
            tune_dir.mkdir(parents=True)
            accept_dir.mkdir()
            baseline_report = run_dir / "bench/baseline/STEP-1/test/output/baseline_benchmark_report.json"
            current_report = round_dir / "bench/validation/current_benchmark_report.json"
            write_report(baseline_report, {"task_a": 0.8})
            write_report(current_report, {"task_a": 0.9})
            baseline_identity = {"bench_model": "baseline-model", "benchmark_version": "1.2.1", "bench_mode": "adapter", "suite": "all", "validation_fixture": {"sha256": "fixture-a"}}
            current_identity = {**baseline_identity, "bench_model": "candidate-model"}
            (run_dir / "bench/bootstrap_manifest.json").write_text(json.dumps({"test_summary": {"overall_score": 0.8}, "test_identity": baseline_identity}), encoding="utf-8")
            (round_dir / "round_state.json").write_text(json.dumps({
                "identity": current_identity,
                "bench": {"optimization": {"summary": {"score": 0.7}}, "validation": {"resultPath": str(current_report), "summary": {"score": 0.9}}},
                "change_summary": {"is_noop": False, "touched_paths": ["skills/skills-local/demo/SKILL.md"]},
                "reachability": {"activated": True}, "change_manifest": {"changes": []},
                "skill_creation": {"valid": True}, "candidate_gate": {"valid": True, "is_noop": False, "reasons": []},
                "candidate_opt_gate": {"valid": True, "effect_gate_passed": True, "protected_gate_passed": True, "reasons": []},
                "full_opt_gate": {"valid": True, "major_regressions": []},
                "full_opt_gate": {"valid": True, "major_regressions": []},
            }), encoding="utf-8")
            paths = {"workspace": str(workspace), "run_dir": run_dir, "round_dir": round_dir, "tune_dir": tune_dir, "accept_dir": accept_dir, "task_id": "EV-3"}
            args = Namespace(round=1, acceptance_margin=0.0, paired_min_runs=1, paired_min_win_rate=0.5, exploration_loss_budget=0.0)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((accept_dir / "acceptance_report.json").read_text())
            self.assertEqual(acceptance["decision"], "passed")
            self.assertEqual(acceptance["promotion_status"], "pending")

    def test_clawweb_stop_requires_accepted_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            round_dir = base / "round-001"
            for child in ("acceptance", "upload", "tune", "spec"):
                (round_dir / child).mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "step_id": "STEP-1",
                "bench": {"optimization": {"summary": {"score": 0.8}}, "validation": {"summary": {"score": 0.99}}},
                "baseline_optimization": {
                    "producer_step_id": "STEP-BASELINE", "domain_owner_id": "owner-1",
                    "domain_id": "train-domain", "bench_run_id": "bench-train",
                    "summary": {"score": 0.7},
                },
                "baseline_validation": {
                    "producer_step_id": "STEP-BASELINE", "domain_owner_id": "owner-1",
                    "domain_id": "test-domain", "bench_run_id": "bench-test",
                    "summary": {"score": 0.8},
                },
            }), encoding="utf-8")
            (round_dir / "acceptance/acceptance_report.json").write_text(json.dumps({
                "accepted": False, "bench_decision": "not_improved",
                "promotion_status": "not_started", "decision": "not_improved", "reason": "rejected",
            }), encoding="utf-8")
            (round_dir / "tune/tune_report.md").write_text("summary", encoding="utf-8")
            (round_dir / "tune/changed_files.txt").write_text("skills/demo/SKILL.md\n", encoding="utf-8")
            (round_dir / "spec/spec_update_report.md").write_text("spec", encoding="utf-8")
            optimize_input = base / "optimize/input"
            optimize_input.mkdir(parents=True)
            (optimize_input / "objective.json").write_text(json.dumps({
                "primary_metric": {
                    "name": "task_success_rate", "display_name": "任务成功率",
                    "operator": ">=", "target": 0.9, "unit": "ratio",
                },
            }), encoding="utf-8")
            paths = {"round_dir": round_dir, "upload_dir": round_dir / "upload", "accept_dir": round_dir / "acceptance", "optimize_input_dir": optimize_input, "task_id": "EV-4"}
            args = Namespace(round=1, step_id="STEP-1", max_rounds=5, clawweb_url="https://example.test", clawweb_url_camel="")
            completed = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD.subprocess, "run", return_value=completed) as run_mock, mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_upload_clawweb(args)
            command = run_mock.call_args.args[0]
            payload = json.loads(command[command.index("--data-raw") + 1])
            self.assertFalse(payload["output"]["roundDecision"]["stop"])
            self.assertFalse(payload["output"]["accepted"])
            self.assertEqual(payload["output"]["promotionStatus"], "not_started")


    def test_change_manifest_requires_root_cause_alternatives_and_one_selected_operator(self):
        manifest = {
            "_source_path": "/tmp/change_manifest.json",
            "selected_proposal_id": "P-001",
            "spec_hypothesis_assessment": "accept for a bounded test",
            "expected_signals": [{"task_id": "task_a", "metric": "score", "baseline": 0.5, "min_delta": 0.1}],
            "protected_signals": [{"task_id": "task_b", "metric": "score", "baseline": 0.9, "max_drop": 0.1}],
            "proposals": [
                {"proposal_id": "P-001", "failure_signature": "early_stop", "suspected_root_cause": "long path", "alternative_causes": ["tool latency"], "selected_operator": "SHORTEN_CRITICAL_PATH", "proposed_change": "shorten", "decision": "selected", "complexity": "low", "impact_radius": "one file", "hypothesis_assessment": "bounded test"},
                {"proposal_id": "P-002", "failure_signature": "early_stop", "suspected_root_cause": "missing checkpoint", "alternative_causes": ["tool latency"], "selected_operator": "ADD_RESULT_VERIFIER", "proposed_change": "checkpoint", "decision": "deferred"},
                {"proposal_id": "P-003", "failure_signature": "early_stop", "suspected_root_cause": "render budget", "alternative_causes": ["tool latency"], "selected_operator": "SEPARATE_ANALYSIS_FROM_RENDERING", "proposed_change": "split rendering", "decision": "rejected"},
            ],
            "edits": [{
                "edit_id": "chg-001", "proposal_id": "P-001", "target_file": "skills/demo/SKILL.md",
                "failure_signature": "early_stop", "suspected_root_cause": "long path",
                "alternative_causes": ["tool latency"], "selected_operator": "SHORTEN_CRITICAL_PATH",
                "falsifiable_prediction": "artifact appears earlier", "rollback_condition": "no improvement",
                "local_check": "check stage order",
            }],
        }
        report = MOD._validate_change_manifest_quality(manifest, {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/demo/SKILL.md"]})
        self.assertTrue(report["valid"], report["errors"])
        manifest["proposals"][1]["decision"] = "selected"
        report = MOD._validate_change_manifest_quality(manifest, {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/demo/SKILL.md"]})
        self.assertFalse(report["valid"])

    def test_hardcode_scan_only_checks_added_lines(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            workspace = base / "workspace"
            target = workspace / "skills/demo/SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("historical task_01_demo\nnew safe guidance\n", encoding="utf-8")
            tune_dir = base / "tune"
            tune_dir.mkdir()
            (tune_dir / "diff.patch").write_text(
                "diff --git a/skills/demo/SKILL.md b/skills/demo/SKILL.md\n"
                "--- a/skills/demo/SKILL.md\n+++ b/skills/demo/SKILL.md\n"
                "@@ -1 +1,2 @@\n historical task_01_demo\n+new safe guidance\n",
                encoding="utf-8",
            )
            summary = {"changed_files": [{"path": "skills/demo/SKILL.md", "change": "modified"}]}
            report = MOD._candidate_hardcode_report({"workspace": str(workspace), "tune_dir": tune_dir}, summary)
            self.assertTrue(report["valid"], report["findings"])
            (tune_dir / "diff.patch").write_text(
                "diff --git a/skills/demo/SKILL.md b/skills/demo/SKILL.md\n"
                "--- a/skills/demo/SKILL.md\n+++ b/skills/demo/SKILL.md\n"
                "@@ -1 +1,2 @@\n historical text\n+special task_15_hidden rule\n",
                encoding="utf-8",
            )
            report = MOD._candidate_hardcode_report({"workspace": str(workspace), "tune_dir": tune_dir}, summary)
            self.assertFalse(report["valid"])

    def test_review_decision_is_validated_and_rendered_to_compact_spec(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            spec_dir = base / "spec"
            spec_dir.mkdir()
            paths = {"spec_dir": spec_dir}
            decision = {
                "schema_version": "evolution.review_decision.v1",
                "round_id": 2,
                "acceptance_decision": "needs_replication",
                "summary": "Current evidence suggests a long critical path, but the claim is not reproduced.",
                "confidence": "low",
                "evidence": [{"capability": "multi_stage_execution", "status": "suspected", "claim": "The first useful artifact appears too late.", "confidence": 0.6}],
                "direction_decisions": [{"direction_id": "DIR-001", "decision": "freeze", "confidence": "low", "rationale": "Single-run evidence is insufficient.", "revisit_condition": "Repeated evidence becomes available."}],
                "next_experiments": [{"experiment_id": "EXP-001", "failure_signature": "long_workflow_early_termination", "operator": "SHORTEN_CRITICAL_PATH", "hypothesis": "Shortening preparation produces the first artifact earlier.", "scope": "one target skill file", "protected_behavior": "output contract remains complete", "acceptance_signal": "first artifact appears before stage 2"}],
            }
            (spec_dir / "review_decision.json").write_text(json.dumps(decision), encoding="utf-8")
            result = MOD._render_review_outputs(paths, 2)
            self.assertTrue(result["ok"], result)
            spec_text = (spec_dir / "spec-v2.md").read_text(encoding="utf-8")
            spec_json = json.loads((spec_dir / "spec-v2.json").read_text(encoding="utf-8"))
            self.assertEqual(spec_json["schema_version"], "evolution.spec.v1")
            self.assertEqual(spec_json["spec_version"], "v2")
            self.assertLess(len(spec_text), 8000)
            self.assertNotIn("SHORTEN_CRITICAL_PATH", spec_text)
            self.assertIn("Experiment Question", spec_text)
            self.assertNotIn("task_", spec_text)
            decision["direction_decisions"][0]["decision"] = "retire"
            self.assertFalse(MOD._validate_review_decision(paths, 2, decision)["valid"])


    def test_real_review_drift_is_normalized_instead_of_failing_round(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            spec_dir = base / "spec"
            round_dir = base / "round-001"
            run_dir = base / "run"
            spec_dir.mkdir()
            round_dir.mkdir()
            opt_report = base / "opt.json"
            val_report = run_dir / "bench/baseline/x/test/output/val.json"
            write_report(opt_report, {"task_04_train": 1.0})
            write_report(val_report, {"task_18_hidden": 0.7})
            (round_dir / "round_state.json").write_text(json.dumps({"bench": {"optimization": {"resultPath": str(opt_report)}, "validation": {"resultPath": ""}}}), encoding="utf-8")
            decision = {
                "schema_version": "evolution.review_decision.v1",
                "round_id": 1,
                "acceptance_decision": "invalid_candidate",
                "summary": "task_04_train is optimization evidence; task_18_hidden must remain held out.",
                "confidence": "low",
                "evidence": [{"capability": "workspace_integrity", "status": "suspected", "claim": "expert_hidden may be contaminated", "confidence": 0.5}],
                "direction_decisions": [],
                "next_experiments": [{
                    "experiment_id": "EXP-001", "failure_signature": "workspace_contamination",
                    "operator": "ENSURE_WORKSPACE_SANITY", "hypothesis": "clean state removes noise",
                    "scope": "inspect workspace only", "protected_behavior": "baseline remains unchanged",
                    "acceptance_signal": "system diff is clean",
                }],
            }
            (spec_dir / "review_decision.json").write_text(json.dumps(decision), encoding="utf-8")
            paths = {"spec_dir": spec_dir, "round_dir": round_dir, "run_dir": run_dir, "optimize_output_dir": base / "missing"}
            result = MOD._render_review_outputs(paths, 1)
            self.assertTrue(result["ok"], result)
            normalized = json.loads((spec_dir / "review_decision.normalized.json").read_text())
            self.assertEqual(normalized["schema_version"], "evolution.review_decision.v2")
            self.assertNotIn("operator", json.dumps(normalized))
            serialized = json.dumps(normalized)
            self.assertIn("task_04_train", serialized)
            self.assertNotIn("task_18_hidden", serialized)
            self.assertNotIn("expert_hidden", serialized)

    def test_successful_watchdog_fallback_does_not_report_terminal_failure(self):
        args = Namespace(resume=False, force=False, round_timeout=0)
        with mock.patch.object(MOD, "_step_policy", return_value={"max_attempts": 1, "fatal": False, "fallback": "copy_previous_spec"}), \
             mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), \
             mock.patch.object(MOD, "_log_block"), mock.patch.object(MOD, "_record_step_failure"), \
             mock.patch.object(MOD, "_report_step_failure_to_clawweb_best_effort") as report_mock, \
             mock.patch.object(MOD, "_apply_step_fallback", return_value={"ok": True, "fallback": "copy_previous_spec"}):
            result = MOD._call_action_for_round_watchdog(args, "ensure-review", lambda _: (_ for _ in ()).throw(RuntimeError("bad review")))
        self.assertEqual(result["status"], "FALLBACK")
        self.assertTrue(report_mock.called)
        self.assertTrue(all(call.kwargs.get("final") is False for call in report_mock.call_args_list))

    def test_upload_oss_has_nonfatal_fallback(self):
        policy = MOD.WATCHDOG_STEP_POLICIES["upload-oss"]
        self.assertFalse(policy["fatal"])
        self.assertEqual(policy["fallback"], "mark_oss_failed")


    def test_section12_round_sequence_is_effect_first(self):
        args = Namespace(skip_oss=True, skip_clawweb=True)
        names = [name for name, _ in MOD._round_sequence(args)]
        self.assertNotIn("bench-opt", names)
        self.assertLess(names.index("load-baseline-opt"), names.index("ensure-tune"))
        self.assertLess(names.index("ensure-tune"), names.index("bench-full-opt"))
        self.assertLess(names.index("bench-full-opt"), names.index("bench-val"))
        self.assertLess(names.index("accept"), names.index("pack"))
        self.assertLess(names.index("pack"), names.index("restore"))

    def test_candidate_opt_gate_blocks_missing_expected_fix_and_protected_regression(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            candidate = base / "candidate.json"
            write_report(candidate, {"task_fix": 0.50, "task_canary": 0.60})
            state = {
                "baseline_optimization": {"task_scores": {"task_fix": 0.50, "task_canary": 0.90}},
                "candidate_opt_signal_plan": {
                    "expected_signals": [{"task_id": "task_fix", "metric": "score", "baseline": 0.50, "min_delta": 0.10}],
                    "protected_signals": [{"task_id": "task_canary", "metric": "score", "baseline": 0.90, "max_drop": 0.10}],
                },
                "bench": {"candidate_optimization_targeted": {"resultPath": str(candidate), "startedAt": 20}},
                "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}},
            }
            report = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            self.assertFalse(report["valid"])
            self.assertFalse(report["effect_gate_passed"])
            self.assertFalse(report["protected_gate_passed"])

    def test_candidate_opt_gate_rejects_pre_tune_report(self):
        with tempfile.TemporaryDirectory() as root:
            report_path = Path(root) / "candidate.json"
            write_report(report_path, {"task_a": 0.8})
            state = {
                "baseline_optimization": {"task_scores": {"task_a": 0.5}},
                "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "task_a", "metric": "score", "baseline": 0.5, "min_delta": 0.1}], "protected_signals": []},
                "bench": {"candidate_optimization_targeted": {"resultPath": str(report_path), "startedAt": 5}},
                "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}},
            }
            gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            self.assertFalse(gate["valid"])
            self.assertFalse(gate["temporal_activation_valid"])

    def test_targeted_selector_adds_runner_protected_canary(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            templates = base / "templates"
            templates.mkdir()
            for tid in ("task_low", "task_fix", "task_high"):
                (templates / f"{tid}.md").write_text(tid, encoding="utf-8")
            paths = {"tune_dir": base / "tune", "round_dir": base / "round"}
            paths["tune_dir"].mkdir(); paths["round_dir"].mkdir()
            manifest = {
                "expected_signals": [{"task_id": "task_fix", "metric": "score", "baseline": 0.4, "min_delta": 0.1}],
                "protected_signals": [],
            }
            (paths["tune_dir"] / "change_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            state = {"baseline_optimization": {"task_scores": {"task_low": 0.2, "task_fix": 0.4, "task_high": 0.95}}}
            args = Namespace(bench_mode="local")
            with mock.patch.object(MOD, "_local_template_dir", return_value=templates):
                plan = MOD._candidate_opt_signal_plan(args, paths, state)
            self.assertIn("task_fix", plan["selected_task_ids"])
            self.assertIn("task_high", plan["selected_task_ids"])
            self.assertEqual(plan["runner_added_canary"], "task_high")

    def test_evaluation_identity_change_invalidates_cache(self):
        base = {"complete": True, "identity_sha256": "a"}
        self.assertTrue(MOD._evaluation_identity_matches(base, dict(base)))
        self.assertFalse(MOD._evaluation_identity_matches(base, {"complete": True, "identity_sha256": "b"}))
        self.assertFalse(MOD._evaluation_identity_matches(base, {"complete": False, "identity_sha256": "a"}))

    def test_review_v2_rejects_implementation_leakage_while_v1_is_normalized(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {"spec_dir": Path(root)}
            v2 = {
                "schema_version": "evolution.review_decision.v2", "summary": "x", "confidence": "low",
                "hypotheses": [{"failure_signature": "f", "status": "suspected", "claim": "c", "alternative_causes": ["a"], "disambiguation_signal": "s", "protected_behaviors": ["p"], "operator": "ADD_RESULT_VERIFIER"}],
                "direction_decisions": [],
            }
            self.assertFalse(MOD._validate_review_decision(paths, 1, v2)["valid"])
            v1 = {"schema_version": "evolution.review_decision.v1", "summary": "x", "next_experiments": [{"failure_signature": "f", "operator": "ADD_RESULT_VERIFIER", "hypothesis": "c", "acceptance_signal": "s", "protected_behavior": "p"}]}
            normalized, _ = MOD._normalize_review_decision(paths, 1, v1)
            self.assertEqual(normalized["schema_version"], "evolution.review_decision.v2")
            self.assertNotIn("operator", json.dumps(normalized))

    def test_review_v2_allows_natural_language_block_status(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            spec_dir = base / "round-001/spec"
            input_dir = base / "round-001/input"
            spec_dir.mkdir(parents=True)
            input_dir.mkdir(parents=True)
            paths = {
                "spec_dir": spec_dir,
                "input_dir": input_dir,
                "round_dir": base / "round-001",
                "optimize_output_dir": base,
            }
            (input_dir / "spec-v0.json").write_text(json.dumps({
                "schema_version": "evolution.spec.v1",
                "spec_version": "v0",
                "objective_contract": {"objective_summary": ["Improve reliability."]},
                "search_contract": {},
                "scope_contract": {},
            }), encoding="utf-8")
            decision = {
                "schema_version": "evolution.review_decision.v2",
                "summary": "Tune is blocked because the target is read-only.",
                "confidence": "low",
                "hypotheses": [{
                    "failure_signature": "read_only_target",
                    "status": "suspected",
                    "claim": "A blocking workspace boundary prevented the experiment.",
                    "alternative_causes": ["runtime_variance"],
                    "disambiguation_signal": "Retry after a writable target becomes available.",
                }],
                "direction_decisions": [{
                    "direction_id": "DIR-001",
                    "decision": "defer",
                    "confidence": "high",
                    "rationale": "The blocker is environmental rather than experimental evidence.",
                    "revisit_condition": "A writable target becomes available.",
                }],
            }
            (spec_dir / "review_decision.json").write_text(json.dumps(decision), encoding="utf-8")
            result = MOD._render_review_outputs(paths, 1)
            self.assertTrue(result["ok"], result)
            self.assertTrue((spec_dir / "spec-v1.json").is_file())
            self.assertTrue((spec_dir / "spec-v1.md").is_file())
            self.assertTrue((spec_dir / "spec_update_report.md").is_file())

    def test_expert_prefix_stripped_keeps_failure_signature_distinct(self):
        """Regression: redaction must strip the `expert_`/`task_<n>_` rubric
        prefix but KEEP the mechanism/suffix name, so different failure
        signatures do not collapse into one `[redacted_rubric]`/[redacted_case]
        dedup key in failure_registry.json."""
        # Two distinct expert_-prefixed signatures must stay distinct after redaction.
        d1 = {"hypotheses": [{"failure_signature": "expert_critical_risk_burial", "status": "suspected", "alternative_causes": ["a"], "revisit_condition": "x"}]}
        d2 = {"hypotheses": [{"failure_signature": "expert_output_duplication", "status": "suspected", "alternative_causes": ["a"], "revisit_condition": "x"}]}
        w1, w2 = [], []
        out1 = MOD._redact_review_value(d1, set(), set(), w1)
        out2 = MOD._redact_review_value(d2, set(), set(), w2)
        sig1 = out1["hypotheses"][0]["failure_signature"]
        sig2 = out2["hypotheses"][0]["failure_signature"]
        self.assertNotIn("expert_", sig1)
        self.assertNotIn("expert_", sig2)
        self.assertNotEqual(sig1, sig2)  # not collapsed to one key
        self.assertEqual(sig1, "critical_risk_burial")
        self.assertEqual(sig2, "output_duplication")
        self.assertTrue(any("stripped rubric/expert prefix" in w for w in w1))
        self.assertNotIn("[redacted_rubric]", sig1 + sig2)

        # task_<n>_<name>: keep the descriptive suffix; pure task_<n>: fully redact.
        dt = {"hypotheses": [{"failure_signature": "task_18_hidden_case", "status": "suspected", "alternative_causes": ["a"], "revisit_condition": "x"}]}
        wt = []
        outt = MOD._redact_review_value(dt, set(), {"task_18_hidden_case"}, wt)
        self.assertNotIn("task_18", outt["hypotheses"][0]["failure_signature"])
        self.assertIn("hidden_case", outt["hypotheses"][0]["failure_signature"])  # suffix kept

        dp = {"hypotheses": [{"failure_signature": "ref task_7 fails", "status": "suspected", "alternative_causes": ["a"], "revisit_condition": "x"}]}
        wp = []
        outp = MOD._redact_review_value(dp, set(), {"task_7"}, wp)
        self.assertNotIn("task_7", outp["hypotheses"][0]["failure_signature"])
        self.assertIn("[redacted_case]", outp["hypotheses"][0]["failure_signature"])  # no suffix => full redact

    def test_spec_utf8_budget_is_deterministic(self):
        text = "中" * 5000
        first, truncated = MOD._fit_utf8_budget(text, 8192)
        second, _ = MOD._fit_utf8_budget(text, 8192)
        self.assertTrue(truncated)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first.encode("utf-8")), 8192)

    def test_repeated_pair_uses_task_x_replicate_unit(self):
        with tempfile.TemporaryDirectory() as root:
            baseline = Path(root) / "baseline.json"
            candidate = Path(root) / "candidate.json"
            write_report(baseline, {"task_a": 0.5, "task_b": 0.6})
            write_report(candidate, {"task_a": 0.8, "task_b": 0.7})
            paired = MOD._build_repeated_paired_eval(Namespace(acceptance_margin=0.0, paired_min_win_rate=0.5, sampling_seed=101), str(baseline), str(candidate))
            self.assertEqual(paired["evaluation_unit"], "task_x_replicate")
            self.assertEqual(paired["replicate_count"], 1)
            self.assertTrue(paired["same_task_set"])
            self.assertTrue(MOD._paired_eval_has_replicates(paired, 1))
            self.assertTrue(all(item["seed"] == 101 for item in paired["pairs"]))


    def test_round_one_loads_bootstrap_train_and_test_without_pre_tune_bench(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            run_dir = base / "run"
            round_dir = run_dir / "optimize/output/round-001"
            input_dir = run_dir / "optimize/input"
            report = run_dir / "bench/baseline/x/train/output/train_benchmark_report.json"
            test_report = run_dir / "bench/baseline/x/test/output/test_benchmark_report.json"
            artifact = input_dir / "baseline/artifact_v0.zip"
            write_report(report, {"task_a": 0.7})
            write_report(test_report, {"task_v": 0.8})
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"zip")
            round_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({"identity": {"optimization_fixture": {"sha256": "fixture"}, "benchmark_version": "1.2.1"}}), encoding="utf-8")
            paths = {"run_dir": run_dir, "round_dir": round_dir, "optimize_input_dir": input_dir, "optimize_output_dir": run_dir / "optimize/output", "task_id": "EV-1"}
            args = Namespace(round=1, model="antchat/GLM-5", suite="all", bench_mode="local", judge="", bench_timeout=10, strict_bench=False)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "action_bench_local") as bench_mock, mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                registry = MOD.action_load_baseline_opt(args)
            bench_mock.assert_not_called()
            self.assertEqual(registry["source"], "bootstrap_train")
            state = json.loads((round_dir / "round_state.json").read_text())
            self.assertEqual(state["bench"]["baseline_optimization"]["cacheStatus"], "bootstrap_hit")
            self.assertEqual(state["bench"]["baseline_validation"]["cacheStatus"], "bootstrap_hit")

    def test_round_one_with_only_train_bootstrap_generates_only_test_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            run_dir = base / "run"
            round_dir = run_dir / "optimize/output/round-001"
            input_dir = run_dir / "optimize/input"
            train_report = run_dir / "bench/baseline/plan/train/output/train_benchmark_report.json"
            artifact = input_dir / "baseline/artifact_v0.zip"
            write_report(train_report, {"task_a": 0.7})
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"zip")
            round_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({"identity": {"optimization_fixture": {"sha256": "fixture"}}}), encoding="utf-8")
            paths = {"run_dir": run_dir, "round_dir": round_dir, "optimize_input_dir": input_dir, "optimize_output_dir": run_dir / "optimize/output", "task_id": "EV-1"}
            args = Namespace(round=1, step_id="STEP-OPT", model="antchat/GLM-5", suite="all", bench_mode="adapter", judge="", bench_timeout=10, strict_bench=False)
            generated_test = run_dir / "bench/baseline/STEP-OPT/test/output/test_benchmark_report.json"
            generated_registry = {
                "schema_version": "evolution.baseline_validation.v1", "available": True,
                "source": "optimize_cache_miss_rerun", "round_id": 0,
                "result_path": str(generated_test), "summary": {"score": 0.8},
                "task_scores": {"task_v": 0.8}, "identity": {},
            }
            def generate_test(*_args, **_kwargs):
                write_report(generated_test, {"task_v": 0.8})
                return generated_registry
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_run_and_register_baseline_role", side_effect=generate_test) as run_mock, mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_load_baseline_opt(args)
            self.assertEqual(run_mock.call_count, 1)
            self.assertEqual(run_mock.call_args.kwargs["kind"], "validation")
            state = json.loads((round_dir / "round_state.json").read_text())
            self.assertEqual(state["bench"]["baseline_optimization"]["cacheStatus"], "bootstrap_hit")
            self.assertEqual(state["bench"]["baseline_validation"]["cacheStatus"], "generated")

    def test_on_demand_baseline_report_is_persisted_outside_round_directory(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = Path(root) / "run"
            source = run_dir / "optimize/output/round-001/bench/baseline_validation/output/demo_benchmark_report.json"
            write_report(source, {"task_v": 0.8})
            paths = {"run_dir": run_dir}
            args = Namespace(round=1, step_id="STEP-1")
            record = MOD._persist_baseline_bench_report(args, paths, role="test", bench_record={
                "status": "succeeded", "resultPath": str(source), "summary": {"score": 0.8},
                "benchRunId": "bench-1", "logPath": "/tmp/bench.log",
            })
            target = run_dir / "bench/baseline/STEP-1/test/output/demo_benchmark_report.json"
            self.assertEqual(record["resultPath"], str(target))
            self.assertTrue(target.is_file())
            self.assertTrue((target.parent.parent / "baseline_result.json").is_file())

    def test_round_one_without_bootstrap_generates_train_and_test_even_for_shared_domain(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = Path(root) / "run"
            round_dir = run_dir / "optimize/output/round-001"
            input_dir = run_dir / "optimize/input"
            artifact = input_dir / "baseline/artifact_v0.zip"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"zip")
            round_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text("{}", encoding="utf-8")
            paths = {"run_dir": run_dir, "round_dir": round_dir, "optimize_input_dir": input_dir, "optimize_output_dir": run_dir / "optimize/output", "task_id": "EV-SHARED"}
            args = Namespace(round=1, step_id="STEP-OPT", model="antchat/GLM-5", suite="all", bench_mode="adapter", judge="", bench_timeout=10, strict_bench=False, train_bench_domain_id="shared", test_bench_domain_id="shared")
            generated_kinds = []
            def generate_role(*_args, **kwargs):
                kind = kwargs["kind"]
                role = "train" if kind == "optimization" else "test"
                generated_kinds.append(kind)
                report = run_dir / f"bench/baseline/STEP-OPT/{role}/output/{role}_benchmark_report.json"
                write_report(report, {f"task_{role}": 0.8})
                return {
                    "schema_version": f"evolution.baseline_{kind}.v1", "available": True,
                    "source": "optimize_cache_miss_rerun", "round_id": 0,
                    "result_path": str(report), "summary": {"score": 0.8},
                    "task_scores": {f"task_{role}": 0.8}, "identity": {},
                }
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_run_and_register_baseline_role", side_effect=generate_role), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_load_baseline_opt(args)
            self.assertEqual(generated_kinds, ["optimization", "validation"])
            manifest = json.loads((run_dir / "optimize/output/optimize_manifest.json").read_text())
            self.assertIn("/train/", manifest["accepted_baseline_optimization"]["result_path"])
            self.assertIn("/test/", manifest["accepted_baseline_validation"]["result_path"])
            self.assertNotIn("last_accepted_round", manifest)

    def test_frozen_domain_cache_can_reuse_fixture_hash_after_round_state_is_archived(self):
        cached = {
            "kind": "validation", "domain_id": "domain-a", "validation_fixture_sha256": "fixture-a",
            "validation_fixture": {"sha256": "fixture-a"}, "cache_complete": True,
        }
        desired = {
            "kind": "validation", "domain_id": "domain-a", "validation_fixture_sha256": "",
            "validation_fixture": {}, "cache_complete": False,
            "cache_missing_fields": ["validation_fixture_sha256"],
        }
        for key in MOD.EVALUATION_IDENTITY_KEYS:
            cached.setdefault(key, "same")
            desired.setdefault(key, "same")
        cached["validation_fixture_sha256"] = "fixture-a"
        desired["validation_fixture_sha256"] = ""
        cached["identity_sha256"] = MOD._stable_json_sha256({key: cached.get(key) for key in MOD.EVALUATION_IDENTITY_KEYS})
        desired["identity_sha256"] = MOD._stable_json_sha256({key: desired.get(key) for key in MOD.EVALUATION_IDENTITY_KEYS})
        self.assertTrue(MOD._evaluation_identity_matches_frozen_task(cached, desired))

    def test_complete_promotes_candidate_full_opt_registry_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            round_dir = base / "output/round-001"
            for child in ("acceptance", "artifacts", "spec", "upload"):
                (round_dir / child).mkdir(parents=True)
            opt = base / "opt.json"; val = base / "val.json"
            write_report(opt, {"task_a": 0.8}); write_report(val, {"task_v": 0.9})
            artifact = round_dir / "artifacts/artifact_v1.zip"; artifact.write_bytes(b"candidate")
            (round_dir / "artifacts/pack_report.json").write_text(json.dumps({
                "status": "success", "artifactPath": str(artifact), "artifactKind": "accepted",
                "sha256": MOD._sha256(artifact),
            }), encoding="utf-8")
            (round_dir / "spec/spec-v1.md").write_text("spec", encoding="utf-8")
            acceptance = {
                "accepted": False, "bench_decision": "passed",
                "promotion_status": "pending", "decision": "passed", "restore_required": False,
            }
            (round_dir / "acceptance/acceptance_report.json").write_text(json.dumps(acceptance), encoding="utf-8")
            (round_dir / "round_state.json").write_text(json.dumps({
                "step_id": "STEP-ROUND-1",
                "bench": {
                    "optimization": {
                        "resultPath": str(opt), "summary": {"score": 0.8},
                        "benchRunId": "bench-candidate-train", "domainId": "train-domain",
                        "domainOwnerId": "owner-1", "producerStepId": "STEP-ROUND-1",
                    },
                    "validation": {
                        "resultPath": str(val), "summary": {"score": 0.9},
                        "benchRunId": "bench-candidate-test", "domainId": "test-domain",
                        "domainOwnerId": "owner-1", "producerStepId": "STEP-ROUND-1",
                    },
                },
                "candidate_opt_gate": {"valid": True}, "change_manifest": {"selected_proposal_id": "P-1"}, "identity": {},
            }), encoding="utf-8")
            paths = {"run_dir": base, "round_dir": round_dir, "optimize_output_dir": base / "output", "accept_dir": round_dir / "acceptance", "artifacts_dir": round_dir / "artifacts", "spec_dir": round_dir / "spec", "task_id": "EV-1"}
            args = Namespace(
                round=1, step_id="STEP-ROUND-1", owner_id="owner-1",
                train_bench_domain_id="train-domain", test_bench_domain_id="test-domain",
                artifact_path="", model="antchat/GLM-5", suite="all", bench_mode="local",
                judge="", bench_timeout=10, strict_bench=False, skip_oss=True,
            )
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_complete(args)
            manifest = json.loads((base / "optimize/output/optimize_manifest.json").read_text())
            self.assertEqual(manifest["accepted_baseline_optimization"]["round_id"], 1)
            self.assertEqual(manifest["accepted_baseline_optimization"]["result_path"], str(opt))
            self.assertEqual(manifest["baseline_registry"]["validation"]["result_path"], str(val))
            self.assertEqual(manifest["accepted_baseline_optimization"]["bench_run_id"], "bench-candidate-train")
            self.assertEqual(manifest["accepted_baseline_optimization"]["producer_step_id"], "STEP-ROUND-1")
            self.assertEqual(manifest["accepted_baseline_optimization"]["domain_id"], "train-domain")
            self.assertEqual(manifest["accepted_baseline_optimization"]["domain_owner_id"], "owner-1")
            self.assertEqual(manifest["accepted_baseline_validation"]["bench_run_id"], "bench-candidate-test")
            self.assertEqual(manifest["accepted_baseline_validation"]["domain_id"], "test-domain")

    def test_clawweb_report_contract_failure_posts_terminal_failure(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            round_dir = base / "round-002"
            for child in ("acceptance", "upload", "tune", "spec"):
                (round_dir / child).mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "step_id": "STEP-2",
                "bench": {
                    "optimization": {"summary": {"score": 0.7}},
                    "validation": {"summary": {"score": 0.6}},
                },
                "baseline_optimization": {"summary": {"score": 0.8}},
                "baseline_validation": {"summary": {"score": 0.7}},
            }), encoding="utf-8")
            (round_dir / "acceptance/acceptance_report.json").write_text(json.dumps({
                "accepted": False, "bench_decision": "not_improved",
                "promotion_status": "not_started", "decision": "not_improved",
            }), encoding="utf-8")
            (round_dir / "tune/tune_report.md").write_text("summary", encoding="utf-8")
            (round_dir / "tune/changed_files.txt").write_text("", encoding="utf-8")
            (round_dir / "spec/spec_update_report.md").write_text("spec", encoding="utf-8")
            optimize_input = base / "optimize/input"
            optimize_input.mkdir(parents=True)
            (optimize_input / "objective.json").write_text(json.dumps({
                "primary_metric": {
                    "name": "task_success_rate", "display_name": "任务成功率",
                    "operator": ">=", "target": 0.9, "unit": "ratio",
                },
            }), encoding="utf-8")
            paths = {
                "round_dir": round_dir, "upload_dir": round_dir / "upload",
                "accept_dir": round_dir / "acceptance", "optimize_input_dir": optimize_input,
                "task_id": "EV-2",
            }
            args = Namespace(
                round=2, step_id="STEP-2", max_rounds=3,
                clawweb_url="https://example.test", clawweb_url_camel="",
            )
            completed = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(
                MOD.subprocess, "run", return_value=completed
            ) as run_mock, mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_upload_clawweb(args)
            command = run_mock.call_args.args[0]
            payload = json.loads(command[command.index("--data-raw") + 1])
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "OPTIMIZE_REPORT_CONTRACT_FAILED")
            self.assertIn("baseline.train", payload["error"]["message"])
            manifest = json.loads((round_dir / "upload/clawweb_manifest.json").read_text())
            self.assertEqual(manifest["status"], "SUCCESS")
            self.assertTrue(manifest["reported_terminal_failure"])


    def test_tune_prompt_reads_loaded_baseline_optimization_not_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); run_dir = base / "run"; round_dir = run_dir / "optimize/output/round-001"
            input_dir = run_dir / "optimize/input"; tune_dir = round_dir / "tune"; spec_input = round_dir / "input"
            report = run_dir / "bench/baseline/x/train/output/train_benchmark_report.json"
            test_report = run_dir / "bench/baseline/x/test/output/test_benchmark_report.json"
            write_report(report, {"task_baseline": 0.73})
            write_report(test_report, {"task_validation": 0.81})
            artifact = input_dir / "baseline/artifact_v0.zip"; artifact.parent.mkdir(parents=True); artifact.write_bytes(b"base")
            for directory in (round_dir, tune_dir, spec_input): directory.mkdir(parents=True, exist_ok=True)
            (input_dir / "objective.md").write_text("objective", encoding="utf-8")
            (spec_input / "spec-v0.json").write_text(json.dumps({"schema_version": "evolution.spec.v1", "spec_version": "v0", "objective_contract": {}, "accepted_baseline_snapshot": {}, "experiment_questions": [], "protected_behaviors": [], "search_contract": {}, "scope_contract": {}, "evaluation_contract": {}}), encoding="utf-8")
            candidate = base / "candidate.json"; write_report(candidate, {"task_candidate_only": 0.99})
            (round_dir / "round_state.json").write_text(json.dumps({"identity": {"optimization_fixture": {"sha256": "fixture"}}, "bench": {"optimization": {"resultPath": str(candidate)}}}), encoding="utf-8")
            paths = {"run_dir": run_dir, "round_dir": round_dir, "optimize_input_dir": input_dir, "optimize_output_dir": run_dir / "optimize/output", "input_dir": spec_input, "tune_dir": tune_dir, "task_id": "EV-1", "skill_base": str(Path(__file__).parents[1]), "workspace": str(base)}
            args = Namespace(round=1, model="antchat/GLM-5", suite="all", bench_mode="local", judge="", bench_timeout=10, strict_bench=False)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_load_baseline_opt(args)
                prompt = MOD._build_tune_prompt(args, paths)
            self.assertIn("task_baseline", prompt)
            self.assertIn("baseline_score: 0.73", prompt)
            self.assertNotIn("optimization bench result not available", prompt.lower())
            self.assertNotIn("task_candidate_only", prompt)
            self.assertIn("<evolution_history>", prompt)
            self.assertIn("只实施一个独立改动", prompt)
            self.assertIn("reasoning_quality", prompt)
            self.assertIn("delivery_reliability", prompt)
            self.assertIn("effect_design", prompt)
            self.assertIn("preserved_mechanisms", prompt)
            self.assertIn("OpenClaw Skill 的可写范围仅限", prompt)
            self.assertIn("skills/skills-local/**", prompt)
            self.assertIn("公共 Skill", prompt)
            self.assertIn("不要跟随公共 Skill 软链", prompt)

    def test_tune_history_excludes_validation_and_reads_structured_effect_memory(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            output = base / "optimize/output"
            output.mkdir(parents=True)
            (output / "optimize_manifest.json").write_text(json.dumps({
                "rounds": [{
                    "round_id": 1, "decision": "rejected", "accepted": False,
                    "optimization_score": 0.8, "validation_score": 0.99, "restore_required": True,
                }]
            }), encoding="utf-8")
            (output / "experiment_ledger.jsonl").write_text(json.dumps({
                "round_id": 1,
                "effect_design": {
                    "change_strategy": "replace",
                    "reasoning_quality_expected": "improve risk reasoning",
                    "delivery_reliability_expected": "maintain complete delivery",
                },
                "complexity_budget": {"critical_path_delta": "neutral", "complexity_risk": "low"},
                "preserved_mechanisms": [{"mechanism": "dialog output first"}],
            }) + "\n", encoding="utf-8")
            args = Namespace(round=2)
            paths = {"run_dir": base, "optimize_output_dir": output}
            tune_history = MOD._build_evolution_history_text(args, paths)
            self.assertIn("optimization_score=0.8", tune_history)
            self.assertNotIn("0.99", tune_history)
            self.assertIn("strategy=replace", tune_history)
            self.assertIn("dialog output first", tune_history)
            review_history = MOD._build_evolution_history_text(args, paths, include_validation=True)
            self.assertIn("validation_score=0.99", review_history)

    def test_diff_stats_capture_hunks_and_load_bearing_removals(self):
        with tempfile.TemporaryDirectory() as root:
            patch = Path(root) / "diff.patch"
            patch.write_text(
                "diff --git a/skills/a.md b/skills/a.md\n"
                "--- a/skills/a.md\n+++ b/skills/a.md\n"
                "@@ -1,2 +1,2 @@\n"
                "-⛔ 对话输出精简原则（防截断核心策略）\n"
                "+新增目标规则\n",
                encoding="utf-8",
            )
            stats = MOD._diff_stats(patch)
            self.assertEqual(stats["hunk_count"], 1)
            self.assertEqual(stats["additions"], 1)
            self.assertEqual(stats["deletions"], 1)
            self.assertEqual(len(stats["removed_instruction_lines"]), 1)
            self.assertIn("防截断", stats["removed_instruction_lines"][0]["line"])

    def test_atomic_experiment_rejects_multiple_files_and_hunks(self):
        manifest = valid_manifest()
        manifest["edits"].append({**manifest["edits"][0], "edit_id": "E2", "target_file": "skills/other.md"})
        summary = {
            "is_noop": False, "source": "system_workspace_diff", "trusted": True,
            "touched_paths": ["skills/demo/SKILL.md", "skills/other.md"],
            "diff": {"nonempty": True, "additions": 2, "deletions": 0, "hunk_count": 2},
        }
        report = MOD._validate_change_manifest_quality(manifest, summary, search_contract={"required_operator_diversity": 3, "required_operator_family_diversity": 3, "max_changed_files": 3, "max_executed_edits": 3})
        self.assertFalse(report["valid"])
        self.assertTrue(any("exactly one executed edit" in e for e in report["errors"]))
        self.assertTrue(any("exactly one changed file" in e for e in report["errors"]))
        self.assertTrue(any("diff hunk" in e for e in report["errors"]))

    def test_atomic_append_rejects_hidden_deletion(self):
        manifest = valid_manifest()
        manifest["effect_design"] = {"change_strategy": "append", "patch_operation": "append"}
        manifest["atomic_experiment"] = {
            "independent_variable": "add one terminology rule",
            "target_file": "skills/demo/SKILL.md",
            "target_anchor": "terminology section",
            "held_constant": ["anti truncation"],
            "confounds_checked": ["no unrelated removal"],
        }
        summary = {
            "is_noop": False, "source": "system_workspace_diff", "trusted": True,
            "touched_paths": ["skills/demo/SKILL.md"],
            "diff": {"nonempty": True, "additions": 1, "deletions": 7, "hunk_count": 1, "removed_instruction_lines": [{"path": "skills/demo/SKILL.md", "line": "防截断核心策略"}]},
        }
        report = MOD._validate_change_manifest_quality(manifest, summary)
        self.assertFalse(report["valid"])
        self.assertTrue(any("patch_operation=append" in e for e in report["errors"]))
        self.assertEqual(report["atomic_experiment"]["removed_instruction_lines"][0]["line"], "防截断核心策略")

    def test_create_skill_normalizes_metadata_and_treats_discovery_link_as_activation_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            tune_dir = Path(root) / "tune"
            skill_dir = workspace / "skills/skills-local/yuque"
            skill_dir.mkdir(parents=True)
            tune_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: yuque\ndescription: Use for Yuque team and knowledge-base queries.\n---\n\nCall the dedicated Yuque MCP tools.\n",
                encoding="utf-8",
            )
            (workspace / "skills/yuque").symlink_to("skills-local/yuque")

            manifest = valid_manifest("skills/skills-local/yuque/SKILL.md")
            manifest["edits"][0]["change_type"] = "CREATE_SKILL"
            manifest["created_skill"] = {
                "name": "yuque",
                "entrypoint": "skills/skills-local/yuque/SKILL.md",
                "discovery_link": "skills/yuque -> skills-local/yuque",
                "trigger_scope": "Yuque team and knowledge-base data queries",
                "negative_trigger_examples": ["general web search", "calendar lookup"],
                "overlap_with_existing_skills": "general search remains responsible for public web queries",
                "why_existing_skills_insufficient": "general search cannot access private Yuque data",
                "rollback_condition": "remove the skill and discovery link",
            }
            (tune_dir / "change_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            loaded = MOD._load_change_manifest({"tune_dir": tune_dir})

            self.assertEqual(loaded["edits"][0]["created_skill"]["name"], "yuque")
            self.assertIn("normalized", " ".join(loaded["_normalization_warnings"]))

            summary = {
                "is_noop": False,
                "source": "system_workspace_diff",
                "trusted": True,
                "touched_paths": ["skills/skills-local/yuque/SKILL.md", "skills/yuque"],
                "agent_report_consistency": {"matches": True},
                "system_diff": {"changed_files": [
                    {"path": "skills/skills-local/yuque/SKILL.md", "after": {"type": "file"}},
                    {"path": "skills/yuque", "after": {"type": "symlink", "target": "skills-local/yuque"}},
                ]},
                "diff": {"nonempty": True, "additions": 5, "deletions": 0, "hunk_count": 1},
            }
            with mock.patch.object(MOD, "_load_mutation_operator_library", return_value={
                "_source_path": "test",
                "operators": [
                    {"name": "SHORTEN_CRITICAL_PATH", "family": "workflow"},
                    {"name": "ADD_RESULT_VERIFIER", "family": "verifier"},
                    {"name": "SEPARATE_ANALYSIS_FROM_RENDERING", "family": "instruction"},
                ],
            }):
                quality = MOD._validate_change_manifest_quality(loaded, summary)

            self.assertTrue(quality["valid"], quality["errors"])
            self.assertEqual(quality["logical_changed_file_count"], 1)
            self.assertEqual(quality["actual_changed_file_count"], 2)
            self.assertEqual(quality["atomic_experiment"]["activation_metadata_paths"], ["skills/yuque"])

            creation = MOD._validate_created_skills(
                Namespace(),
                {"workspace": workspace, "tune_dir": tune_dir},
                loaded,
                [
                    {"path": "skills/skills-local/yuque/SKILL.md", "change": "added"},
                    {"path": "skills/yuque", "change": "added"},
                ],
            )
            self.assertTrue(creation["valid"], creation["errors"])

    def test_create_skill_rejects_same_name_copy_of_existing_public_skill(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            tune_dir = Path(root) / "tune"
            skill_dir = workspace / "skills/skills-local/mcporter"
            skill_dir.mkdir(parents=True)
            tune_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: mcporter\ndescription: Private override.\n---\n\nDo work.\n",
                encoding="utf-8",
            )
            (workspace / "skills/mcporter").symlink_to("skills-local/mcporter")
            (tune_dir / "system-before-manifest.json").write_text(json.dumps({
                "entries": {
                    "skills/mcporter": {"type": "symlink", "target": "/opt/openclaw/skills/mcporter"},
                },
            }), encoding="utf-8")

            manifest = valid_manifest("skills/skills-local/mcporter/SKILL.md")
            manifest["edits"][0]["change_type"] = "CREATE_SKILL"
            manifest["created_skill"] = {
                "name": "mcporter",
                "entrypoint": "skills/skills-local/mcporter/SKILL.md",
                "discovery_link": "skills/mcporter -> skills-local/mcporter",
                "trigger_scope": "MCP server discovery and invocation for configured tools",
                "negative_trigger_examples": ["general web search", "calendar lookup"],
                "why_existing_skills_insufficient": "claimed missing behavior",
                "rollback_condition": "remove the private override",
            }
            (tune_dir / "change_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            loaded = MOD._load_change_manifest({"tune_dir": tune_dir})
            report = MOD._validate_created_skills(
                Namespace(),
                {"workspace": workspace, "tune_dir": tune_dir},
                loaded,
                [
                    {"path": "skills/skills-local/mcporter/SKILL.md", "change": "added"},
                    {"path": "skills/mcporter", "change": "modified"},
                ],
            )

            self.assertFalse(report["valid"])
            self.assertTrue(any("already existed before Tune" in error for error in report["errors"]))

    def test_spec_canonicalization_forces_atomic_execution_budget(self):
        spec = {
            "schema_version": "evolution.spec.v1", "spec_version": "v0",
            "objective_contract": {}, "accepted_baseline_snapshot": {},
            "experiment_questions": [], "protected_behaviors": [],
            "search_contract": {"required_operator_diversity": 5, "max_changed_files": 5, "max_executed_edits": 5},
            "scope_contract": {}, "evaluation_contract": {},
        }
        canonical, _ = MOD._canonicalize_spec_for_tune(spec)
        contract = canonical["search_contract"]
        self.assertEqual(contract["required_operator_diversity"], 5)
        self.assertEqual(contract["max_changed_files"], 1)
        self.assertEqual(contract["max_executed_edits"], 1)
        self.assertEqual(contract["max_diff_hunks"], 1)
        self.assertTrue(contract["atomic_single_variable"])

    def test_effect_design_manifest_extensions_are_backward_compatible(self):
        manifest = valid_manifest()
        manifest["effect_design"] = {
            "change_strategy": "replace",
            "minimum_change_rationale": "smallest behavior-changing edit",
            "reasoning_quality_expected": "improve",
            "delivery_reliability_expected": "maintain",
            "scorer_blind_spot_checks": ["duplicate blocks"],
        }
        manifest["complexity_budget"] = {
            "instruction_token_delta_estimate": "neutral",
            "mandatory_step_delta": 0,
            "guide_read_delta": 0,
            "validator_call_delta": 0,
            "repair_loop_delta": 0,
            "critical_path_delta": "neutral",
            "offsetting_removals_or_merges": [],
            "one_in_one_out_satisfied": True,
            "complexity_risk": "low",
        }
        manifest["preserved_mechanisms"] = [{
            "mechanism": "complete delivery", "source": "baseline", "preservation_check": "four parts once",
        }]
        report = MOD._validate_change_manifest_quality(
            manifest,
            {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/demo/SKILL.md"]},
        )
        self.assertTrue(report["valid"], report["errors"])

    def test_expected_and_protected_baseline_spoof_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            candidate = Path(root) / "candidate.json"
            write_report(candidate, {"task_fix": 0.6, "task_safe": 0.6})
            state = {
                "baseline_optimization": {"task_scores": {"task_fix": 0.9, "task_safe": 0.9}},
                "candidate_opt_signal_plan": {
                    "expected_signals": [{"task_id": "task_fix", "metric": "score", "baseline": 0.1, "expected_min": 0.5}],
                    "protected_signals": [{"task_id": "task_safe", "metric": "score", "baseline": 0.1, "max_drop": 0.1}],
                },
                "bench": {"candidate_optimization_targeted": {"resultPath": str(candidate), "startedAt": 20}},
                "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}},
            }
            gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            self.assertFalse(gate["valid"])
            self.assertGreaterEqual(sum("declared baseline mismatch" in reason for reason in gate["reasons"]), 2)
            self.assertEqual(gate["expected_results"][0]["baseline"]["value"], 0.9)

    def test_metric_registry_score_breakdown_derived_and_unobservable(self):
        with tempfile.TemporaryDirectory() as root:
            report = Path(root) / "x_benchmark_report.json"
            report.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.8, "breakdown": {"llm_judge.风险识别准确度": 0.7, "stage_completion": 4}}]}}]}), encoding="utf-8")
            self.assertEqual(MOD._extract_candidate_metric(report, "task_a", "score")["value"], 0.8)
            exact = MOD._extract_candidate_metric(report, "task_a", "breakdown.llm_judge.风险识别准确度")
            self.assertTrue(exact["observable"]); self.assertEqual(exact["value"], 0.7)
            derived = MOD._extract_candidate_metric(report, "task_a", "stage_completion")
            self.assertTrue(derived["observable"]); self.assertEqual(derived["value"], 4.0)
            missing = MOD._extract_candidate_metric(report, "task_a", "unknown_behavior")
            self.assertFalse(missing["observable"]); self.assertEqual(missing["error"], "unobservable_metric")

    def test_tiny_random_score_delta_is_not_behavior_change(self):
        with tempfile.TemporaryDirectory() as root:
            candidate = Path(root) / "candidate.json"; write_report(candidate, {"task_a": 0.9000000001})
            state = {"baseline_optimization": {"task_scores": {"task_a": 0.9}}, "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "task_a", "metric": "score", "min_delta": 0.0}], "protected_signals": [{"task_id": "task_a", "metric": "score", "max_drop": 0.1}]}, "bench": {"candidate_optimization_targeted": {"resultPath": str(candidate), "startedAt": 20}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            self.assertFalse(gate["behavior_changed"])
            self.assertFalse(gate["valid"])

    def test_expected_derived_metric_improvement_passes_with_protection(self):
        with tempfile.TemporaryDirectory() as root:
            baseline = Path(root) / "baseline.json"; candidate = Path(root) / "candidate.json"
            baseline.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.9, "breakdown": {"stage_completion": 1}}]}}]}), encoding="utf-8")
            candidate.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.88, "breakdown": {"stage_completion": 4}}]}}]}), encoding="utf-8")
            state = {"baseline_optimization": {"result_path": str(baseline), "task_scores": {"task_a": 0.9}}, "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "task_a", "metric": "stage_completion", "expected_min": 4}], "protected_signals": [{"task_id": "task_a", "metric": "score", "max_drop": 0.05}]}, "bench": {"candidate_optimization_targeted": {"resultPath": str(candidate), "startedAt": 20}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            self.assertTrue(gate["valid"], gate["reasons"])
            self.assertTrue(gate["behavior_changed"])

    def test_operator_family_diversity_and_file_budget_fail_closed(self):
        base_proposal = {"failure_signature": "f", "suspected_root_cause": "r", "alternative_causes": ["a"], "proposed_change": "c"}
        manifest = {"_source_path": "/tmp/m", "selected_proposal_id": "P1", "spec_hypothesis_assessment": {"decision": "accept", "reason": "e", "unresolved_alternatives": []}, "expected_signals": [{"task_id": "a", "metric": "score", "min_delta": 0.1}], "protected_signals": [{"task_id": "b", "metric": "score", "max_drop": 0.1}], "proposals": [
            {**base_proposal, "proposal_id": "P1", "selected_operator": "SHORTEN_CRITICAL_PATH", "decision": "selected", "complexity": "low", "impact_radius": "one"},
            {**base_proposal, "proposal_id": "P2", "selected_operator": "ADD_EXECUTION_CHECKPOINT", "decision": "deferred"},
            {**base_proposal, "proposal_id": "P3", "selected_operator": "ADD_FALLBACK_BUDGET", "decision": "rejected"}],
            "edits": [{"edit_id": f"E{i}", "proposal_id": "P1", "target_file": f"skills/f{i}.md", "failure_signature": "f", "suspected_root_cause": "r", "alternative_causes": ["a"], "selected_operator": "SHORTEN_CRITICAL_PATH", "falsifiable_prediction": "p", "rollback_condition": "r", "local_check": "c"} for i in range(4)]}
        report = MOD._validate_change_manifest_quality(manifest, {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": [f"skills/f{i}.md" for i in range(4)]}, search_contract={"required_operator_diversity": 3, "max_changed_files": 3, "max_executed_edits": 4})
        self.assertFalse(report["valid"])
        self.assertTrue(any("family diversity" in warning for warning in report["research_quality"]["warnings"]))
        self.assertTrue(any("changed file budget" in error for error in report["errors"]))
        self.assertTrue(report["operator_library_path"].endswith("mutation_operator_library.json"))

    def test_tune_prefers_spec_json_and_legacy_markdown_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); input_dir = base / "input"; input_dir.mkdir()
            paths = {"input_dir": input_dir}
            args = Namespace(round=2)
            (input_dir / "spec-v1.md").write_text("Failure Mode: markdown_failure\noperator: BAD", encoding="utf-8")
            (input_dir / "spec-v1.json").write_text(json.dumps({"schema_version": "evolution.spec.v1", "spec_version": "v1", "objective_contract": {}, "accepted_baseline_snapshot": {}, "experiment_questions": [{"failure_signature": "json_failure"}], "protected_behaviors": [], "search_contract": {}, "scope_contract": {}, "evaluation_contract": {}}), encoding="utf-8")
            spec, source = MOD._load_input_spec_contract(args, paths)
            self.assertEqual(source, "json_v1"); self.assertEqual(spec["experiment_questions"][0]["failure_signature"], "json_failure")
            (input_dir / "spec-v1.json").unlink()
            legacy, source = MOD._load_input_spec_contract(args, paths)
            self.assertEqual(source, "markdown_legacy_normalized")
            self.assertNotIn("selected_operator", json.dumps(legacy))
            self.assertNotIn("patch_generator_instruction", json.dumps(legacy))

    def test_review_skill_contains_only_v2_decision_boundary(self):
        text = (Path(__file__).parents[1] / "clawevolve-review/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("evolution.review_decision.v2", text)
        self.assertNotIn("## Active Optimization Directions", text)
        self.assertNotIn("## Suggested Direction", text)
        self.assertNotIn("next_experiments", text)
        self.assertIn("不得直接写 spec-vN.md", text)

    def test_failure_registry_is_updated_without_validation_details(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); spec_dir = base / "round/spec"; spec_dir.mkdir(parents=True)
            paths = {"spec_dir": spec_dir, "optimize_output_dir": base / "output", "skill_base": str(Path(__file__).parents[1])}
            decision = {"hypotheses": [{"failure_signature": "early_stop", "status": "suspected", "alternative_causes": ["latency"], "revisit_condition": "repeat"}]}
            result = MOD._update_failure_registry(paths, 1, decision)
            registry = json.loads(Path(result["path"]).read_text())
            self.assertEqual(registry["failures"][0]["observation_count"], 1)
            self.assertNotRegex(json.dumps(registry), r"task_\d+|expert_")


    def test_accepted_rejected_next_round_registry_hit_without_baseline_bench(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"; run_dir = workspace / "clawevolve_results/EV-CACHE"; output = run_dir / "optimize/output"
            artifact = output / "round-001/artifacts/artifact_v1.zip"; artifact.parent.mkdir(parents=True); artifact.write_bytes(b"accepted")
            opt_report = Path(root) / "accepted_opt.json"; val_report = Path(root) / "accepted_val.json"
            write_report(opt_report, {"task_a": 0.8}); write_report(val_report, {"task_v": 0.9})
            common_identity = {"optimization_fixture": {"sha256": "opt-fixture"}, "validation_fixture": {"sha256": "val-fixture"}, "benchmark_version": "1.2.1", "suite": "all"}
            args1 = Namespace(round=1, model="antchat/GLM-5", suite="all", bench_mode="local", judge="judge", bench_timeout=10, strict_bench=False, sampling_seed=7, temperature=0.0, max_tokens=1000)
            paths1 = {"round_dir": output / "round-001", "workspace": str(workspace), "skill_base": str(Path(__file__).parents[1]), "optimize_output_dir": output, "run_dir": run_dir, "task_id": "EV-CACHE"}
            (paths1["round_dir"] / "round_state.json").write_text(json.dumps({"identity": common_identity}), encoding="utf-8")
            identity = MOD._evaluation_identity(args1, paths1, kind="optimization", artifact_path=artifact, report_path=opt_report)
            validation_identity = MOD._evaluation_identity(args1, paths1, kind="validation", artifact_path=artifact, report_path=val_report)
            self.assertTrue(identity["cache_complete"], identity["cache_missing_fields"])
            registry = {"schema_version": "evolution.baseline_optimization.v1", "round_id": 1, "result_path": str(opt_report), "summary": {"score": 0.8}, "task_scores": {"task_a": 0.8}, "identity": identity}
            validation_registry = {"schema_version": "evolution.baseline_validation.v1", "round_id": 1, "result_path": str(val_report), "summary": {"score": 0.9}, "task_scores": {"task_v": 0.9}, "identity": validation_identity}
            manifest_path = output / "optimize_manifest.json"; manifest_path.write_text(json.dumps({"schema_version": "evolution.run_manifest.v0", "task_id": "EV-CACHE", "evolve_run_id": "EV-CACHE", "rounds": [], "last_accepted_round": 1, "last_accepted_artifact": {"localPath": str(artifact)}, "accepted_baseline_optimization": registry, "accepted_baseline_validation": validation_registry}), encoding="utf-8")

            # Complete a rejected round and prove the accepted registry is unchanged.
            round2 = output / "round-002"
            for child in ("acceptance", "artifacts", "spec", "upload"): (round2 / child).mkdir(parents=True, exist_ok=True)
            (round2 / "acceptance/acceptance_report.json").write_text(json.dumps({
                "accepted": False, "bench_decision": "not_improved",
                "promotion_status": "not_started", "decision": "not_improved", "restore_required": True,
            }), encoding="utf-8")
            (round2 / "round_state.json").write_text(json.dumps({"identity": common_identity, "baseline_artifact": str(artifact), "bench": {"optimization": {"resultPath": str(opt_report), "summary": {"score": 0.7}}, "validation": {"resultPath": str(val_report), "summary": {"score": 0.7}}}}), encoding="utf-8")
            paths2 = {"run_dir": run_dir, "round_dir": round2, "optimize_output_dir": output, "accept_dir": round2 / "acceptance", "artifacts_dir": round2 / "artifacts", "spec_dir": round2 / "spec", "task_id": "EV-CACHE", "workspace": str(workspace), "skill_base": str(Path(__file__).parents[1])}
            args2 = Namespace(round=2, artifact_path="", model="antchat/GLM-5", suite="all", bench_mode="local", judge="judge", bench_timeout=10, strict_bench=False, sampling_seed=7, temperature=0.0, max_tokens=1000)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths2), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_complete(args2)
            after_reject = json.loads(manifest_path.read_text())
            self.assertEqual(after_reject["accepted_baseline_optimization"]["identity"]["identity_sha256"], identity["identity_sha256"])

            round3 = output / "round-003"; round3.mkdir()
            (round3 / "round_state.json").write_text(json.dumps({"identity": common_identity, "baseline_artifact": str(artifact)}), encoding="utf-8")
            paths3 = {"run_dir": run_dir, "round_dir": round3, "optimize_input_dir": run_dir / "optimize/input", "optimize_output_dir": output, "task_id": "EV-CACHE", "workspace": str(workspace), "skill_base": str(Path(__file__).parents[1])}
            args3 = Namespace(round=3, model="antchat/GLM-5", suite="all", bench_mode="local", judge="judge", bench_timeout=10, strict_bench=False, sampling_seed=7, temperature=0.0, max_tokens=1000)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths3), mock.patch.object(MOD, "action_bench_local") as bench_mock, mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                loaded = MOD.action_load_baseline_opt(args3)
            bench_mock.assert_not_called()
            state3 = json.loads((round3 / "round_state.json").read_text())
            self.assertEqual(state3["bench"]["baseline_optimization"]["cacheStatus"], "registry_hit")
            self.assertEqual(loaded["round_id"], 1)


    def test_non_score_metric_at_threshold_without_delta_fails_effect_gate(self):
        with tempfile.TemporaryDirectory() as root:
            baseline = Path(root) / "b.json"; candidate = Path(root) / "c.json"
            for path in (baseline, candidate):
                path.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.9, "breakdown": {"stage_completion": 4}}]}}]}), encoding="utf-8")
            state = {"baseline_optimization": {"result_path": str(baseline)}, "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "task_a", "metric": "stage_completion", "expected_min": 4}], "protected_signals": [{"task_id": "task_a", "metric": "score", "max_drop": 0.1}]}, "bench": {"candidate_optimization_targeted": {"resultPath": str(candidate), "startedAt": 20}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            result = gate["expected_results"][0]
            self.assertTrue(result["threshold_passed"])
            self.assertFalse(result["change_passed"])
            self.assertEqual(result["required_min_delta"], 1.0)
            self.assertFalse(gate["behavior_changed"]); self.assertFalse(gate["effect_gate_passed"]); self.assertFalse(gate["valid"])

    def test_activation_probe_cannot_replace_business_effect(self):
        with tempfile.TemporaryDirectory() as root:
            candidate = Path(root) / "c.json"; write_report(candidate, {"task_a": 0.9})
            state = {"baseline_optimization": {"task_scores": {"task_a": 0.9}}, "activation_probe": {"passed": True}, "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "task_a", "metric": "score", "expected_min": 0.9}], "protected_signals": [{"task_id": "task_a", "metric": "score", "max_drop": 0.1}]}, "bench": {"candidate_optimization_targeted": {"resultPath": str(candidate), "startedAt": 20}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            self.assertFalse(gate["behavior_changed"]); self.assertFalse(gate["effect_gate_passed"])

    def test_decrease_expected_metric_passes(self):
        with tempfile.TemporaryDirectory() as root:
            baseline = Path(root) / "b.json"; candidate = Path(root) / "c.json"
            baseline.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.9, "breakdown": {"failure_signature_count": 3}}]}}]}), encoding="utf-8")
            candidate.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.9, "breakdown": {"failure_signature_count": 1}}]}}]}), encoding="utf-8")
            state = {"baseline_optimization": {"result_path": str(baseline)}, "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "task_a", "metric": "failure_signature_count", "direction": "decrease", "min_delta": 1, "expected_min": 1}], "protected_signals": [{"task_id": "task_a", "metric": "score", "max_drop": 0.1}]}, "bench": {"candidate_optimization_targeted": {"resultPath": str(candidate), "startedAt": 20}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
            self.assertTrue(gate["valid"], gate["reasons"])
            self.assertEqual(gate["expected_results"][0]["delta"], -2.0)

    def test_expected_signal_maintain_direction_is_invalid(self):
        manifest = valid_manifest(); manifest["expected_signals"][0]["direction"] = "maintain"
        summary = {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/demo/SKILL.md"]}
        report = MOD._validate_change_manifest_quality(manifest, summary)
        self.assertFalse(report["valid"]); self.assertTrue(any("direction is invalid" in error for error in report["errors"]))

    def test_system_diff_file_budget_and_manifest_coverage(self):
        manifest = valid_manifest()
        summary = {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": [f"skills/f{i}.md" for i in range(5)]}
        report = MOD._validate_change_manifest_quality(manifest, summary, search_contract={"max_changed_files": 3})
        self.assertFalse(report["valid"]); self.assertEqual(report["actual_changed_file_count"], 5)
        self.assertTrue(any("changed file budget exceeded" in error for error in report["errors"]))
        self.assertTrue(any("unreported changed files" in error for error in report["errors"]))
        complete = valid_manifest("skills/a.md"); complete["edits"][0]["affected_files"] = ["./skills/b.md"]
        ok = MOD._validate_change_manifest_quality(complete, {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/a.md", "skills/b.md"]})
        self.assertFalse(ok["valid"])
        self.assertTrue(any("exactly one changed file" in error for error in ok["errors"]))
        phantom = valid_manifest("skills/a.md")
        bad = MOD._validate_change_manifest_quality(phantom, {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/b.md"]})
        self.assertFalse(bad["valid"]); self.assertEqual(bad["phantom_declared_files"], ["skills/a.md"])
        untrusted = MOD._validate_change_manifest_quality(valid_manifest(), {"is_noop": False, "source": "system_workspace_diff", "trusted": False, "touched_paths": ["skills/demo/SKILL.md"]})
        self.assertFalse(untrusted["valid"]); self.assertFalse(untrusted["system_diff_trusted"])

    def test_create_skill_discovery_link_requires_explicit_coverage(self):
        manifest = valid_manifest("skills/skills-local/demo/SKILL.md")
        manifest["edits"][0]["affected_files"] = ["skills/demo"]
        summary = {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/skills-local/demo/SKILL.md", "skills/demo"]}
        atomic_report = MOD._validate_change_manifest_quality(manifest, summary)
        self.assertFalse(atomic_report["valid"])
        self.assertTrue(any("exactly one changed file" in error for error in atomic_report["errors"]))
        manifest["edits"][0].pop("affected_files")
        report = MOD._validate_change_manifest_quality(manifest, summary)
        self.assertFalse(report["valid"]); self.assertIn("skills/demo", report["unreported_changed_files"])

    def test_review_acceptance_payload_excludes_legacy_gates(self):
        acc = {"candidate_opt_gate": {"valid": False, "effect_gate_passed": False, "protected_gate_passed": True, "behavior_changed": False, "expected_results": [{"task_id": "task_99_hidden", "metric": "score", "candidate": {"value": 0.5}, "note": "expert_secret"}], "protected_results": [{"task_id": "task_train", "metric": "score", "passed": True}], "reasons": ["task_99_hidden expert_secret"]}, "full_opt_gate": {"valid": False, "mean_delta": -0.08, "compared_task_count": 12, "major_regressions": [{"task_id": "task_99_hidden"}] * 3, "missing_task_ids": [], "full_coverage": True}}
        payload = MOD._acceptance_for_review(acc, {"task_99_hidden"})
        self.assertNotIn("candidate_opt_gate", payload); self.assertNotIn("full_opt_gate", payload)
        serialized = json.dumps(payload)
        self.assertNotIn("task_99_hidden", serialized); self.assertNotIn("expert_secret", serialized)
        self.assertNotIn("task_train", serialized)

    def test_protected_behavior_resolution_preserves_semantics(self):
        output = MOD._resolve_protected_behavior("complete_output_contract")
        self.assertEqual(output["metric"], "output_contract_complete")
        stage = MOD._resolve_protected_behavior("complete_stage_2_to_4")
        self.assertEqual((stage["metric"], stage["min_value"]), ("stage_completion", 4))
        unknown = MOD._resolve_protected_behavior("custom_unknown_behavior")
        self.assertIsNone(unknown["metric"]); self.assertEqual(unknown["metric_resolution_status"], "unresolved")
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); tune = base / "tune"; tune.mkdir(); templates = base / "templates"; templates.mkdir(); (templates / "task_a.md").write_text("x")
            (tune / "change_manifest.json").write_text(json.dumps({"expected_signals": [], "protected_signals": []}))
            state = {"input_spec_contract": {"protected_behaviors": [unknown]}, "baseline_optimization": {"task_scores": {"task_a": 0.9}}}
            with mock.patch.object(MOD, "_local_template_dir", return_value=templates):
                plan = MOD._candidate_opt_signal_plan(Namespace(bench_mode="local"), {"tune_dir": tune, "round_dir": base}, state)
            self.assertFalse(plan["valid"]); self.assertTrue(any("unresolved protected behavior" in error for error in plan["errors"]))

    def test_legacy_implementation_values_are_sanitized(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir = Path(root); args = Namespace(round=2); paths = {"input_dir": input_dir}
            legacy = {"schema_version": "evolution.spec.v0", "spec_version": "v1", "failure_modes_to_address": [{"failure_mode": "early_stop", "expected_effect": "Add selective anchors to SKILL.md and rewrite Block A/B/C", "claim": "modify skills/demo/SKILL.md", "evidence": "use ADD_RESULT_VERIFIER", "suggested_direction": "exact patch"}]}
            (input_dir / "spec-v1.json").write_text(json.dumps(legacy), encoding="utf-8")
            spec, _ = MOD._load_input_spec_contract(args, paths)
            serialized = json.dumps(spec, ensure_ascii=False)
            for leaked in ("selective anchors", "SKILL.md", "Block A/B/C", "skills/demo", "ADD_RESULT_VERIFIER"):
                self.assertNotIn(leaked, serialized)
            self.assertIn("需要区分 early_stop", serialized)

    def test_canonical_spec_budget_covers_json_markdown_and_tune_payload(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); spec_dir = base / "spec"; input_dir = base / "input"; round_dir = base / "round"; output = base / "output"
            for d in (spec_dir, input_dir, round_dir, output): d.mkdir()
            prior = {"schema_version": "evolution.spec.v1", "spec_version": "v0", "objective_contract": {"objective_summary": ["goal"]}, "accepted_baseline_snapshot": {}, "experiment_questions": [], "protected_behaviors": [], "search_contract": {}, "scope_contract": {}, "evaluation_contract": {}}
            (input_dir / "spec-v0.json").write_text(json.dumps(prior), encoding="utf-8")
            (round_dir / "round_state.json").write_text("{}")
            decision = {"schema_version": "evolution.review_decision.v2", "summary": "s" * 3000, "confidence": "low", "hypotheses": [{"hypothesis_id": "H1", "failure_signature": "f", "status": "suspected", "claim": "长" * 4000, "alternative_causes": ["因" * 500] * 6, "disambiguation_signal": "信" * 3000, "protected_behaviors": [{"behavior_id": "complete_output_contract", "description": "保" * 1000, "metric": "output_contract_complete", "direction": "maintain", "min_value": 1, "max_drop": 0}], "revisit_condition": "复" * 2000}], "direction_decisions": []}
            (spec_dir / "review_decision.json").write_text(json.dumps(decision), encoding="utf-8")
            paths = {"spec_dir": spec_dir, "input_dir": input_dir, "round_dir": round_dir, "optimize_output_dir": output, "skill_base": str(Path(__file__).parents[1])}
            first = MOD._render_review_outputs(paths, 1); first_json = (spec_dir / "spec-v1.json").read_bytes(); first_md = (spec_dir / "spec-v1.md").read_bytes()
            second = MOD._render_review_outputs(paths, 1)
            self.assertTrue(first["ok"] and second["ok"])
            self.assertEqual(first_json, (spec_dir / "spec-v1.json").read_bytes()); self.assertEqual(first_md, (spec_dir / "spec-v1.md").read_bytes())
            spec = json.loads(first_json); payload = json.dumps(MOD._tune_spec_prompt_payload(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            self.assertLessEqual(len(payload), 8192); self.assertLessEqual(len(first_md), 8192)
            self.assertIn(spec["experiment_questions"][0]["claim"], first_md.decode())
            report = (spec_dir / "spec_update_report.md").read_text(); self.assertIn("tune_payload_chars / bytes", report); self.assertIn("truncated_fields", report)

    def test_external_long_v1_spec_is_budgeted_on_load(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir = Path(root)
            spec = {"schema_version": "evolution.spec.v1", "spec_version": "v1", "objective_contract": {"objective_summary": ["g"]}, "accepted_baseline_snapshot": {}, "experiment_questions": [{"failure_signature": "f", "claim": "长" * 5000, "alternative_causes": ["a" * 500] * 6, "disambiguation_signal": "d" * 3000, "revisit_condition": "r" * 1000}], "protected_behaviors": [], "search_contract": {}, "scope_contract": {}, "evaluation_contract": {}}
            (input_dir / "spec-v1.json").write_text(json.dumps(spec), encoding="utf-8")
            loaded, _ = MOD._load_input_spec_contract(Namespace(round=2), {"input_dir": input_dir})
            payload = json.dumps(MOD._tune_spec_prompt_payload(loaded), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            self.assertLessEqual(len(payload), 8192); self.assertLessEqual(len(loaded["experiment_questions"][0]["claim"]), 500)

    def test_transcript_instruction_stage_mentions_do_not_count_as_completion(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); report = base / "x_benchmark_report.json"
            report.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.5}]}}]}))
            transcript = base / "x_transcripts"; transcript.mkdir(); (transcript / "task_a.jsonl").write_text("Instructions: execute Stage 1, Stage 2, Stage 3, Stage 4. No stages were completed.")
            metric = MOD._extract_candidate_metric(report, "task_a", "stage_completion")
            self.assertFalse(metric["observable"])




    def test_not_improved_candidate_is_packed_before_restore(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            artifacts = base / "round-001/artifacts"
            artifacts.mkdir(parents=True)
            paths = {
                "round_dir": base / "round-001", "skill_base": str(base / "skills"),
                "workspace": str(base / "workspace"), "task_id": "EV-REJECTED",
                "run_dir": base,
            }
            args = Namespace(
                round=1, max_artifact_mb=100, pack_include_evolve_results=False,
                pack_dry_run=False,
            )

            def fake_pack(_cmd, **_kwargs):
                (artifacts / "generated.zip").write_bytes(b"rejected-candidate")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            acceptance = {"bench_decision": "not_improved", "decision": "not_improved", "accepted": False}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(
                MOD, "find_script", return_value=base / "pack.sh"
            ), mock.patch.object(MOD, "_require_acceptance_report", return_value=acceptance), mock.patch.object(
                MOD.subprocess, "run", side_effect=fake_pack
            ), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_pack(args)

            report = json.loads((artifacts / "pack_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "success")
            self.assertEqual(report["artifactKind"], "rejected")
            self.assertEqual(report["promotionStatus"], "not_started")
            self.assertTrue(Path(report["artifactPath"]).is_file())


class Section12ProtectedBindingAndNumericTests(unittest.TestCase):
    """Adversarial regressions for candidate opt gate signal binding, protected
    min_value/max_drop gating, boolean_flip direction, numeric fail-closed and
    canonical spec allowlist."""

    def _signal_plan(self, state):
        import tempfile
        templates = Path(tempfile.mkdtemp())
        (templates / "task_x.md").write_text("x", encoding="utf-8")
        tune = Path(tempfile.mkdtemp()); round_dir = Path(tempfile.mkdtemp())
        args = Namespace(bench_mode="local")
        with mock.patch.object(MOD, "_local_template_dir", return_value=templates):
            return MOD._candidate_opt_signal_plan(args, {"tune_dir": tune, "round_dir": round_dir}, state)

    def _effect(self, state):
        return MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})

    def _mk_reports(self, base_breakdown, cand_breakdown, base_score=0, cand_score=0):
        import tempfile
        d = Path(tempfile.mkdtemp()); b = d / "b.json"; c = d / "c.json"
        b.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": base_score, "breakdown": base_breakdown}]}}]}), encoding="utf-8")
        c.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": cand_score, "breakdown": cand_breakdown}]}}]}), encoding="utf-8")
        return b, c

    # ---- P0-1 protected behavior binding ----
    def test_resolved_protected_behavior_unbound_is_invalid(self):
        state = {"baseline_optimization": {"task_scores": {"task_x": 0.9}},
                 "input_spec_contract": {"protected_behaviors": [{"behavior_id": "complete_output_contract", "metric": "output_contract_complete", "min_value": 1, "max_drop": 0, "metric_resolution_status": "resolved"}]},
                 "change_manifest": {"expected_signals": [{"task_id": "task_x", "metric": "score", "baseline": 0.9, "min_delta": 0.05}], "protected_signals": [{"task_id": "task_x", "metric": "score", "baseline": 0.9, "max_drop": 0.1}]}}
        plan = self._signal_plan(state)
        self.assertFalse(plan["valid"])
        self.assertTrue(any("complete_output_contract" in e for e in plan["errors"]))

    def test_resolved_protected_behavior_metric_swapped_to_score_is_invalid(self):
        state = {"baseline_optimization": {"task_scores": {"task_x": 0.9}},
                 "input_spec_contract": {"protected_behaviors": [{"behavior_id": "complete_output_contract", "metric": "output_contract_complete", "min_value": 1, "max_drop": 0, "metric_resolution_status": "resolved"}]},
                 "change_manifest": {"expected_signals": [{"task_id": "task_x", "metric": "score", "baseline": 0.9, "min_delta": 0.05}], "protected_signals": [{"task_id": "task_x", "behavior_id": "complete_output_contract", "metric": "score", "baseline": 0.9, "max_drop": 0.1, "min_value": 1}]}}
        plan = self._signal_plan(state)
        self.assertFalse(plan["valid"])
        self.assertTrue(any("must equal spec metric" in e for e in plan["errors"]))

    def test_resolved_protected_behavior_max_drop_widened_is_invalid(self):
        state = {"baseline_optimization": {"task_scores": {"task_x": 0.9}},
                 "input_spec_contract": {"protected_behaviors": [{"behavior_id": "complete_output_contract", "metric": "output_contract_complete", "min_value": 1, "max_drop": 0, "metric_resolution_status": "resolved"}]},
                 "change_manifest": {"expected_signals": [{"task_id": "task_x", "metric": "score", "baseline": 0.9, "min_delta": 0.05}], "protected_signals": [{"task_id": "task_x", "behavior_id": "complete_output_contract", "metric": "output_contract_complete", "baseline": 0.9, "max_drop": 0.1, "min_value": 1}]}}
        plan = self._signal_plan(state)
        self.assertFalse(plan["valid"])
        self.assertTrue(any("max_drop" in e and "wider" in e for e in plan["errors"]))

    def test_resolved_protected_behavior_min_value_lowered_is_invalid(self):
        state = {"baseline_optimization": {"task_scores": {"task_x": 0.9}},
                 "input_spec_contract": {"protected_behaviors": [{"behavior_id": "complete_output_contract", "metric": "output_contract_complete", "min_value": 1, "max_drop": 0, "metric_resolution_status": "resolved"}]},
                 "change_manifest": {"expected_signals": [{"task_id": "task_x", "metric": "score", "baseline": 0.9, "min_delta": 0.05}], "protected_signals": [{"task_id": "task_x", "behavior_id": "complete_output_contract", "metric": "output_contract_complete", "baseline": 0.9, "max_drop": 0, "min_value": 0}]}}
        plan = self._signal_plan(state)
        self.assertFalse(plan["valid"])
        self.assertTrue(any("min_value" in e and ">=" in e for e in plan["errors"]))

    def test_resolved_protected_behavior_correct_binding_is_valid(self):
        state = {"baseline_optimization": {"task_scores": {"task_x": 0.9}},
                 "input_spec_contract": {"protected_behaviors": [{"behavior_id": "complete_output_contract", "metric": "output_contract_complete", "min_value": 1, "max_drop": 0, "metric_resolution_status": "resolved"}]},
                 "change_manifest": {"expected_signals": [{"task_id": "task_x", "metric": "score", "baseline": 0.9, "min_delta": 0.05}], "protected_signals": [{"task_id": "task_x", "behavior_id": "complete_output_contract", "metric": "output_contract_complete", "baseline": 0.9, "max_drop": 0, "min_value": 1}]}}
        plan = self._signal_plan(state)
        self.assertTrue(plan["valid"], plan["errors"])

    # ---- P0-1 protected min_value gate ----
    def _protected_state(self, base_breakdown, cand_breakdown):
        b, c = self._mk_reports(base_breakdown, cand_breakdown)
        return {"baseline_optimization": {"result_path": str(b), "task_scores": {"t": base_breakdown.get("output_contract_complete")}},
                "bench": {"candidate_optimization_targeted": {"resultPath": str(c), "startedAt": 20}},
                "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}},
                "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "t", "metric": "score", "baseline": 0, "expected_min": 0.1, "min_delta": 0.1}], "protected_signals": [{"task_id": "t", "metric": "output_contract_complete", "baseline": base_breakdown.get("output_contract_complete"), "min_value": 1, "max_drop": 0}]}}

    def test_protected_min_value_baseline_zero_candidate_zero_fails(self):
        state = self._protected_state({"output_contract_complete": 0}, {"output_contract_complete": 0})
        r = self._effect(state); pr = r["protected_results"][0]
        self.assertFalse(pr["passed"]); self.assertFalse(pr["minimum_passed"]); self.assertTrue(pr["drop_passed"])

    def test_protected_min_value_baseline_one_candidate_one_passes(self):
        state = self._protected_state({"output_contract_complete": 1}, {"output_contract_complete": 1})
        r = self._effect(state); pr = r["protected_results"][0]
        self.assertTrue(pr["passed"]); self.assertTrue(pr["minimum_passed"]); self.assertTrue(pr["drop_passed"])

    def test_protected_min_value_drop_and_minimum_both_fail(self):
        state = self._protected_state({"output_contract_complete": 1}, {"output_contract_complete": 0})
        r = self._effect(state); pr = r["protected_results"][0]
        self.assertFalse(pr["passed"]); self.assertFalse(pr["drop_passed"]); self.assertFalse(pr["minimum_passed"])
        self.assertEqual(pr.get("minimum"), 1)

    # ---- P0-2 boolean_flip direction ----
    def _bool_gate(self, baseline, candidate, expected_value=1):
        b, c = self._mk_reports({"output_contract_complete": baseline}, {"output_contract_complete": candidate}, base_score=baseline, cand_score=candidate)
        item = {"task_id": "t", "metric": "output_contract_complete", "baseline": baseline, "direction": "boolean_flip", "expected_min": 1, "min_delta": 0.1}
        if expected_value is not None:
            item["expected_value"] = expected_value
        state = {"baseline_optimization": {"result_path": str(b), "task_scores": {"t": baseline}},
                 "bench": {"candidate_optimization_targeted": {"resultPath": str(c), "startedAt": 20}},
                 "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}},
                 "candidate_opt_signal_plan": {"expected_signals": [item], "protected_signals": []}}
        return MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})["expected_results"][0]

    def test_boolean_flip_zero_to_one_passes(self):
        self.assertTrue(self._bool_gate(0, 1)["passed"])

    def test_boolean_flip_one_to_zero_fails_with_reason(self):
        r = self._bool_gate(1, 0)
        self.assertFalse(r["passed"]); self.assertFalse(r["change_passed"])
        self.assertTrue("expected_value" in r["change_reason"])

    def test_boolean_flip_one_to_one_fails(self):
        self.assertFalse(self._bool_gate(1, 1)["passed"])

    def test_boolean_flip_zero_to_zero_fails(self):
        self.assertFalse(self._bool_gate(0, 0)["passed"])

    def test_boolean_flip_default_expected_value_is_one(self):
        r = self._bool_gate(0, 1, expected_value=None)
        self.assertTrue(r["passed"]); self.assertEqual(r["expected_value"], 1.0)

    # ---- P0-3 numeric fail-closed ----
    def _bad_manifest(self, expected, protected):
        ops = ["SHORTEN_CRITICAL_PATH", "ADD_RESULT_VERIFIER", "SEPARATE_ANALYSIS_FROM_RENDERING"]
        return {"proposals": [{"proposal_id": f"P{i}", "failure_signature": "f", "suspected_root_cause": "r", "alternative_causes": ["a"], "selected_operator": ops[i-1], "proposed_change": "c", "decision": ("selected" if i == 1 else "deferred"), "complexity": "low", "impact_radius": "x"} for i in (1, 2, 3)],
                "selected_proposal_id": "P1", "spec_hypothesis_assessment": {"decision": "accept", "reason": "e", "unresolved_alternatives": []},
                "expected_signals": [expected], "protected_signals": [protected],
                "edits": [{"edit_id": "E1", "proposal_id": "P1", "target_file": "skills/f.md", "failure_signature": "f", "suspected_root_cause": "r", "alternative_causes": ["a"], "selected_operator": "SHORTEN_CRITICAL_PATH", "falsifiable_prediction": "p", "rollback_condition": "r", "local_check": "c"}]}

    def test_manifest_accepts_boolean_flip_with_expected_value_without_threshold_or_delta(self):
        expected = {"task_id": "task_a", "metric": "output_contract_complete", "baseline": 0.0, "direction": "boolean_flip", "expected_value": 1.0}
        protected = {"task_id": "task_b", "metric": "score", "direction": "maintain", "max_drop": 0.1}
        manifest = self._bad_manifest(expected, protected)
        manifest["_source_path"] = "change_manifest.json"
        rep = MOD._validate_change_manifest_quality(
            manifest,
            {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/f.md"]},
            search_contract={"required_operator_diversity": 3, "required_operator_family_diversity": 3, "max_changed_files": 3, "max_executed_edits": 4},
        )
        self.assertTrue(rep["valid"], rep["errors"])

    def test_manifest_still_requires_threshold_or_delta_for_numeric_direction(self):
        expected = {"task_id": "task_a", "metric": "score", "baseline": 0.3, "direction": "increase"}
        protected = {"task_id": "task_b", "metric": "score", "direction": "maintain", "max_drop": 0.1}
        rep = MOD._validate_change_manifest_quality(
            self._bad_manifest(expected, protected),
            {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/f.md"]},
            search_contract={"required_operator_diversity": 3, "required_operator_family_diversity": 3, "max_changed_files": 3, "max_executed_edits": 4},
        )
        self.assertFalse(rep["valid"])
        self.assertTrue(any("expected_min or min_delta" in error for error in rep["errors"]))

    def test_manifest_rejects_non_numeric_expected_min(self):
        rep = MOD._validate_change_manifest_quality(self._bad_manifest({"task_id": "task_a", "metric": "score", "direction": "increase", "min_delta": 0.1, "expected_min": "bad"}, {"task_id": "task_b", "metric": "score", "direction": "maintain", "max_drop": 0.1}), {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/f.md"]}, search_contract={"required_operator_diversity": 3, "max_changed_files": 3, "max_executed_edits": 4})
        self.assertFalse(rep["valid"]); self.assertTrue(any("expected_min" in e and "finite" in e for e in rep["errors"]))

    def test_manifest_rejects_non_numeric_max_drop(self):
        rep = MOD._validate_change_manifest_quality(self._bad_manifest({"task_id": "task_a", "metric": "score", "direction": "increase", "min_delta": 0.1, "expected_min": 0.5}, {"task_id": "task_b", "metric": "score", "direction": "maintain", "max_drop": "bad"}), {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/f.md"]}, search_contract={"required_operator_diversity": 3, "max_changed_files": 3, "max_executed_edits": 4})
        self.assertFalse(rep["valid"]); self.assertTrue(any("max_drop" in e and "finite" in e for e in rep["errors"]))

    def test_manifest_rejects_nan_min_delta(self):
        rep = MOD._validate_change_manifest_quality(self._bad_manifest({"task_id": "task_a", "metric": "score", "direction": "increase", "min_delta": float("nan"), "expected_min": 0.5}, {"task_id": "task_b", "metric": "score", "direction": "maintain", "max_drop": 0.1}), {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/f.md"]}, search_contract={"required_operator_diversity": 3, "max_changed_files": 3, "max_executed_edits": 4})
        self.assertFalse(rep["valid"]); self.assertTrue(any("min_delta" in e and "finite" in e for e in rep["errors"]))

    def test_manifest_rejects_bool_min_value(self):
        rep = MOD._validate_change_manifest_quality(self._bad_manifest({"task_id": "task_a", "metric": "score", "direction": "increase", "min_delta": 0.1, "expected_min": 0.5}, {"task_id": "task_b", "metric": "score", "direction": "maintain", "max_drop": 0.1, "min_value": True}), {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/f.md"]}, search_contract={"required_operator_diversity": 3, "max_changed_files": 3, "max_executed_edits": 4})
        self.assertFalse(rep["valid"]); self.assertTrue(any("min_value" in e and "finite" in e for e in rep["errors"]))

    def test_evaluator_fail_closed_on_bad_expected_min_no_throw(self):
        import tempfile
        d = Path(tempfile.mkdtemp()); c = d / "c.json"; b = d / "b.json"
        b.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": 0}]}}]}), encoding="utf-8")
        c.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": 1}]}}]}), encoding="utf-8")
        state = {"baseline_optimization": {"result_path": str(b), "task_scores": {"t": 0}}, "bench": {"candidate_optimization_targeted": {"resultPath": str(c), "startedAt": 20}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}, "candidate_opt_signal_plan": {"expected_signals": [{"task_id": "t", "metric": "score", "baseline": 0, "expected_min": "bad", "min_delta": 0.1}], "protected_signals": []}}
        gate = MOD._candidate_opt_effect_report(state, {**state["candidate_opt_signal_plan"], "valid": True})
        self.assertFalse(gate["valid"]); self.assertTrue(any("expected_min" in r and "finite" in r for r in gate["reasons"]))

    # ---- P1-1 canonical spec allowlist ----
    def test_canonical_spec_drops_unknown_field_and_reports_budget(self):
        spec = {"schema_version": "evolution.spec.v1", "spec_version": "v1", "objective_contract": {}, "accepted_baseline_snapshot": {}, "experiment_questions": [], "protected_behaviors": [], "search_contract": {}, "scope_contract": {}, "evaluation_contract": {}, "unknown_blob": "x" * 50000}
        canon, budget = MOD._canonicalize_spec_for_tune(spec)
        self.assertNotIn("unknown_blob", canon)
        self.assertEqual(budget["unknown_fields_removed"], ["unknown_blob"])
        self.assertLessEqual(budget["canonical_json_bytes"], 8192)
        self.assertLessEqual(budget["tune_payload_bytes"], 8192)
        self.assertFalse(budget["warning_over_4KB"])
        self.assertIn("canonical_json_over_8KB", budget)
        self.assertIn("tune_payload_over_8KB", budget)

    def test_canonical_spec_warning_over_4kb_reflects_canonical_json(self):
        # objective_summary items are clipped to 1000 chars each; four filled items
        # push the canonical JSON past 4KB, so warning_over_4KB must be true even when
        # the prior implementation only inspected the payload subset.
        spec = {"schema_version": "evolution.spec.v1", "spec_version": "v1", "objective_contract": {"objective_summary": ["y" * 1000, "y" * 1000, "y" * 1000, "y" * 1000]}, "accepted_baseline_snapshot": {}, "experiment_questions": [], "protected_behaviors": [], "search_contract": {}, "scope_contract": {}, "evaluation_contract": {}}
        canon, budget = MOD._canonicalize_spec_for_tune(spec)
        self.assertGreater(budget["canonical_json_bytes"], 4096)
        self.assertTrue(budget["warning_over_4KB"])

    def test_load_input_spec_contract_discards_unknown_field(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            d = Path(root); spec = {"schema_version": "evolution.spec.v1", "spec_version": "v1", "objective_contract": {"objective_summary": ["g"]}, "accepted_baseline_snapshot": {}, "experiment_questions": [], "protected_behaviors": [], "search_contract": {}, "scope_contract": {}, "evaluation_contract": {}, "unknown_blob": "z" * 50000}
            (d / "spec-v1.json").write_text(json.dumps(spec), encoding="utf-8")
            loaded, _ = MOD._load_input_spec_contract(Namespace(round=2), {"input_dir": d})
            self.assertNotIn("unknown_blob", loaded)
            self.assertEqual(loaded["_budget"]["unknown_fields_removed"], ["unknown_blob"])
            self.assertLessEqual(loaded["_budget"]["canonical_json_bytes"], 8192)

class CandidateGateRegressionTests(unittest.TestCase):
    def test_system_modified_skill_is_not_reclassified_as_created_by_agent_patch(self):
        with tempfile.TemporaryDirectory() as root:
            tune = Path(root)
            path = "skills/skills-local/demo/SKILL.md"
            (tune / "diff.patch").write_text(
                f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n",
                encoding="utf-8",
            )
            created = MOD._created_skill_entrypoints_from_changes(
                {"tune_dir": tune}, [{"path": path, "change": "modified"}]
            )
            self.assertEqual(created, [])

    def test_system_created_skill_remains_subject_to_create_skill_contract(self):
        with tempfile.TemporaryDirectory() as root:
            tune = Path(root)
            path = "skills/skills-local/demo/SKILL.md"
            (tune / "diff.patch").write_text("", encoding="utf-8")
            created = MOD._created_skill_entrypoints_from_changes(
                {"tune_dir": tune}, [{"path": path, "change": "created"}]
            )
            self.assertEqual(created, [path])

    def test_signal_schema_normalizes_concrete_alias_and_ignores_aggregate_selector(self):
        manifest = {
            "expected_signals": [
                {"task_id_or_group": "task_04_demo", "metric": "score", "min_delta": 0.1},
                {"task_id_or_group": "overall", "metric": "score", "min_delta": 0.1},
            ],
            "protected_signals": [{"task_id_or_group": "all_tasks_checks", "metric": "score", "max_drop": 0.1}],
        }
        normalized = MOD._normalize_manifest_signal_schema(manifest)
        self.assertEqual(normalized["expected_signals"][0]["task_id"], "task_04_demo")
        self.assertEqual(len(normalized["expected_signals"]), 1)
        self.assertEqual(normalized["protected_signals"], [])
        self.assertEqual(len(normalized["_ignored_signals"]), 2)

    def test_runner_adds_score_canary_and_detaches_mismatched_behavior_annotation(self):
        spec = {"protected_behaviors": [{
            "behavior_id": "PB-001", "metric": "score", "metric_resolution_status": "resolved",
            "min_value": None, "max_drop": 0.1,
        }]}
        manifest = {"protected_signals": [{
            "behavior_id": "PB-001", "task_id": "task_a",
            "metric": "llm_judge.输出格式合规", "baseline": 1.0, "max_drop": 0.1,
        }]}
        normalized = MOD._apply_runner_protected_binding_defaults(spec, manifest, {"task_a": 0.7, "task_b": 0.9})
        self.assertNotIn("behavior_id", normalized["protected_signals"][0])
        canary = normalized["protected_signals"][1]
        self.assertEqual((canary["behavior_id"], canary["task_id"], canary["metric"]), ("PB-001", "task_b", "score"))
        report = MOD._protected_signal_binding_report(spec, normalized["protected_signals"], {"task_a", "task_b"})
        self.assertTrue(report["valid"], report)

    def test_edit_alternative_causes_inherit_from_own_proposal(self):
        manifest = {
            "proposals": [{"proposal_id": "P-1", "alternative_causes": ["latency"]}],
            "edits": [{"proposal_id": "P-1", "alternative_causes": []}],
        }
        normalized = MOD._normalize_manifest_signal_schema(manifest)
        self.assertEqual(normalized["edits"][0]["alternative_causes"], ["latency"])
        self.assertTrue(any("inherited" in warning for warning in normalized["_normalization_warnings"]))

    def test_trusted_consistent_single_mechanism_auto_attributes_unreported_file(self):
        manifest = valid_manifest("skills/a.md")
        summary = {
            "is_noop": False, "source": "system_workspace_diff", "trusted": True,
            "touched_paths": ["skills/a.md", "skills/b.md"],
            "agent_report_consistency": {"matches": True},
        }
        report = MOD._validate_change_manifest_quality(manifest, summary)
        self.assertFalse(report["valid"])
        self.assertTrue(any("exactly one changed file" in error for error in report["errors"]))
        self.assertEqual(report["auto_attributed_changed_files"], ["skills/b.md"])
        self.assertEqual(report["unreported_changed_files"], [])

    def test_signal_plan_ignores_unknown_extra_protected_signal_but_not_expected_signal(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); tune = base / "tune"; templates = base / "templates"
            tune.mkdir(); templates.mkdir(); (templates / "task_a.md").write_text("x", encoding="utf-8")
            state = {
                "baseline_optimization": {"task_scores": {"task_a": 0.9}},
                "change_manifest": {
                    "expected_signals": [{"task_id": "task_a", "metric": "score", "baseline": 0.9, "min_delta": 0.05}],
                    "protected_signals": [
                        {"task_id": "task_a", "metric": "score", "baseline": 0.9, "max_drop": 0.1},
                        {"task_id": "task_a_output", "metric": "output_contract_complete", "max_drop": 0.0},
                    ],
                },
            }
            with mock.patch.object(MOD, "_local_template_dir", return_value=templates):
                plan = MOD._candidate_opt_signal_plan(Namespace(bench_mode="local"), {"tune_dir": tune, "round_dir": base}, state)
            self.assertTrue(plan["valid"], plan)
            self.assertTrue(any("task_a_output" in warning for warning in plan["warnings"]))
            self.assertNotIn("task_a_output", plan["selected_task_ids"])

            state["change_manifest"]["expected_signals"].append({"task_id": "task_missing", "metric": "score", "min_delta": 0.1})
            with mock.patch.object(MOD, "_local_template_dir", return_value=templates):
                invalid = MOD._candidate_opt_signal_plan(Namespace(bench_mode="local"), {"tune_dir": tune, "round_dir": base}, state)
            self.assertFalse(invalid["valid"])
            self.assertTrue(any("task_missing" in error for error in invalid["errors"]))

    def test_runner_does_not_relax_explicit_matching_binding_thresholds(self):
        spec = {"protected_behaviors": [{
            "behavior_id": "PB-001", "metric": "score", "metric_resolution_status": "resolved",
            "min_value": 0.8, "max_drop": 0.1,
        }]}
        manifest = {"protected_signals": [{
            "behavior_id": "PB-001", "task_id": "task_a", "metric": "score",
            "baseline": 0.9, "min_value": 0.7, "max_drop": 0.2,
        }]}
        normalized = MOD._apply_runner_protected_binding_defaults(spec, manifest, {"task_a": 0.9})
        report = MOD._protected_signal_binding_report(spec, normalized["protected_signals"], {"task_a"})
        self.assertFalse(report["valid"])
        self.assertTrue(any("min_value" in error or "max_drop" in error for error in report["errors"]))

    def test_candidate_opt_gate_surfaces_invalid_signal_plan_errors(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); tune = base / "tune"; round_dir = base / "round"
            tune.mkdir(); round_dir.mkdir()
            error = "resolved protected behavior requires Tune binding: PB-001"
            (tune / "candidate_opt_signal_plan.json").write_text(json.dumps({"valid": False, "errors": [error]}), encoding="utf-8")
            (round_dir / "round_state.json").write_text(json.dumps({"candidate_gate": {"valid": True}}), encoding="utf-8")
            paths = {"tune_dir": tune, "round_dir": round_dir}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                report = MOD.action_candidate_opt_gate(Namespace(expected_min_delta=0.01, protected_max_drop=0.1))
            self.assertFalse(report["valid"])
            self.assertEqual(report["reasons"], [error])

    def test_protected_behavior_binding_is_static_gate_contract(self):
        spec = {"protected_behaviors": [{
            "behavior_id": "PB-output", "metric": "output_contract_complete",
            "metric_resolution_status": "resolved", "min_value": 1, "max_drop": 0,
        }]}
        missing = MOD._protected_signal_binding_report(spec, [{
            "task_id": "task_a", "metric": "output_contract_complete",
            "min_value": 1, "max_drop": 0,
        }])
        self.assertFalse(missing["valid"])
        self.assertTrue(any("PB-output" in error for error in missing["errors"]))
        bound = MOD._protected_signal_binding_report(spec, [{
            "behavior_id": "PB-output", "task_id": "task_a",
            "metric": "output_contract_complete", "min_value": 1, "max_drop": 0,
        }])
        self.assertTrue(bound["valid"], bound)

class CandidateGateArchitectureTests(unittest.TestCase):
    def test_invalid_signal_plan_is_persisted_before_targeted_early_return(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); round_dir = base / "round"; tune = round_dir / "tune"
            tune.mkdir(parents=True)
            state_path = round_dir / "round_state.json"
            state_path.write_text(json.dumps({"candidate_gate": {"valid": True}}), encoding="utf-8")
            paths = {"round_dir": round_dir, "tune_dir": tune}
            plan = {"schema_version": "evolution.candidate_opt_signal_plan.v2", "plan_id": "p", "valid": False, "compile_status": "invalid", "errors": ["bad selector"]}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_candidate_opt_signal_plan", return_value=plan), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                out = MOD.action_bench_candidate_opt_targeted(Namespace())
            persisted = json.loads(state_path.read_text())
            self.assertEqual(out["status"], "skipped")
            self.assertEqual(persisted["candidate_opt_signal_plan"], plan)
            self.assertEqual(persisted["candidate_opt_signal_plan_status"], "invalid")
            self.assertEqual(json.loads((tune / "candidate_opt_signal_plan.json").read_text()), plan)
            self.assertEqual(persisted["bench"]["candidate_optimization_targeted"]["skipType"], "invalid_selector")

    def test_candidate_opt_gate_reports_targeted_eval_not_run_not_effect_failure(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); tune = base / "tune"; round_dir = base / "round"
            tune.mkdir(); round_dir.mkdir()
            plan = {"schema_version": "evolution.candidate_opt_signal_plan.v2", "plan_id": "p", "valid": False, "errors": ["bad plan"]}
            (round_dir / "round_state.json").write_text(json.dumps({"candidate_gate": {"valid": True}, "candidate_opt_signal_plan": plan}), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value={"round_dir": round_dir, "tune_dir": tune}), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                report = MOD.action_candidate_opt_gate(Namespace(expected_min_delta=0.01, protected_max_drop=0.1))
            self.assertEqual(report["decision"], "invalid_signal_plan")
            self.assertEqual(report["evaluation_status"], "not_run")
            self.assertIsNone(report["effect_gate_passed"])
            self.assertIsNone(report["protected_gate_passed"])
            self.assertIsNone(report["behavior_changed"])
            self.assertEqual(report["expected_results"], [])

    def test_supporting_expected_failure_does_not_block_required_effect(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); baseline = base / "b.json"; candidate = base / "c.json"
            write_report(baseline, {"task_required": 0.5, "task_support": 0.8, "task_protect": 0.9})
            write_report(candidate, {"task_required": 0.7, "task_support": 0.7, "task_protect": 0.9})
            plan = {"valid": True, "plan_id": "p", "expected_signals": [
                {"task_id": "task_required", "metric": "score", "baseline": 0.5, "min_delta": 0.1, "role": "required"},
                {"task_id": "task_support", "metric": "score", "baseline": 0.8, "min_delta": 0.1, "role": "supporting"},
            ], "protected_signals": [{"task_id": "task_protect", "metric": "score", "baseline": 0.9, "max_drop": 0.1}]}
            state = {"baseline_optimization": {"result_path": str(baseline)}, "bench": {"candidate_optimization_targeted": {"status": "succeeded", "resultPath": str(candidate), "startedAt": 20, "signalPlanId": "p"}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            gate = MOD._candidate_opt_effect_report(state, plan)
            self.assertTrue(gate["valid"], gate["reasons"])
            self.assertTrue(gate["required_expected_results"][0]["passed"])
            self.assertFalse(gate["supporting_expected_results"][0]["passed"])

    def test_budget_and_supporting_protected_failures_do_not_block_targeted_effect(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); baseline = base / "b.json"; candidate = base / "c.json"
            baseline.write_text(json.dumps({"tasks": [
                {"task_id": "task_fix", "grading": {"runs": [{"score": 0.3, "breakdown": {"automated.fix": 0}}]}},
                {"task_id": "task_budget", "grading": {"runs": [{"score": 0.9, "breakdown": {"llm_judge.format": 1}}]}},
            ]}), encoding="utf-8")
            candidate.write_text(json.dumps({"tasks": [
                {"task_id": "task_fix", "grading": {"runs": [{"score": 0.7, "breakdown": {"automated.fix": 1}}]}},
                {"task_id": "task_budget", "grading": {"runs": [{"score": 0.6, "breakdown": {"llm_judge.format": 0}}]}},
            ]}), encoding="utf-8")
            plan = {"schema_version": "evolution.candidate_opt_signal_plan.v2", "valid": True, "plan_id": "p", "expected_signals": [
                {"task_id": "task_fix", "metric": "automated.fix", "baseline": 0, "direction": "boolean_flip", "expected_value": 1, "role": "required"}
            ], "protected_signals": [
                {"behavior_id": "PB-score-breadth", "task_id": "task_budget", "metric": "score", "baseline": 0.9, "max_drop": 0.1, "gate": "budget"},
                {"task_id": "task_budget", "metric": "llm_judge.format", "baseline": 1, "max_drop": 0, "gate": "supporting"},
            ]}
            state = {"baseline_optimization": {"result_path": str(baseline)}, "bench": {"candidate_optimization_targeted": {"status": "succeeded", "resultPath": str(candidate), "startedAt": 20, "signalPlanId": "p"}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            gate = MOD._candidate_opt_effect_report(state, plan)
            self.assertTrue(gate["valid"], gate["reasons"])
            self.assertFalse(gate["budget_protected_results"][0]["passed"])
            self.assertFalse(gate["supporting_protected_results"][0]["passed"])
            self.assertTrue(gate["protected_gate_passed"])

    def test_pb_score_breadth_alias_is_budget_not_hard(self):
        behavior = MOD._resolve_protected_behavior("PB-score-breadth")
        self.assertEqual((behavior["metric"], behavior["gate"]), ("score", "budget"))
        drifted = MOD._resolve_protected_behavior({"behavior_id": "PB-score-breadth", "metric": None, "max_drop": None})
        self.assertEqual((drifted["metric"], drifted["gate"], drifted["max_drop"]), ("score", "budget", 0.1))

    def test_namespaced_breakdown_metric_is_directly_observable(self):
        with tempfile.TemporaryDirectory() as root:
            report = Path(root) / "report.json"
            report.write_text(json.dumps({"tasks": [{"task_id": "task_a", "grading": {"runs": [{"score": 0.5, "breakdown": {"llm_judge.输出格式合规": 1.0, "automated.expert_check": 1.0}}]}}]}), encoding="utf-8")
            judge = MOD._extract_candidate_metric(report, "task_a", "llm_judge.输出格式合规")
            automated = MOD._extract_candidate_metric(report, "task_a", "automated.expert_check")
            self.assertTrue(judge["observable"]); self.assertEqual(judge["value"], 1.0)
            self.assertTrue(automated["observable"]); self.assertEqual(automated["source"], "breakdown")

    def test_diff_provenance_failure_is_not_reported_as_hardcode(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"; tune = base / "tune"
            target = workspace / "skills/demo.md"; target.parent.mkdir(parents=True); target.write_text("safe", encoding="utf-8")
            tune.mkdir(); (tune / "diff.patch").write_text("", encoding="utf-8")
            summary = {"changed_files": [{"path": "skills/demo.md", "change": "modified"}]}
            provenance = MOD._candidate_diff_provenance_report({"workspace": str(workspace), "tune_dir": tune}, summary)
            hardcode = MOD._candidate_hardcode_report({"workspace": str(workspace), "tune_dir": tune}, summary)
            self.assertFalse(provenance["valid"])
            self.assertTrue(hardcode["valid"])
            self.assertEqual(hardcode["findings"], [])

    def test_repeated_eval_accumulates_distinct_replicates(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            b1=base/"b1.json"; c1=base/"c1.json"; b2=base/"b2.json"; c2=base/"c2.json"
            write_report(b1, {"task_a": 0.5}); write_report(c1, {"task_a": 0.7})
            write_report(b2, {"task_a": 0.6}); write_report(c2, {"task_a": 0.8})
            args = Namespace(acceptance_margin=0.0, paired_min_win_rate=0.5, sampling_seed=101)
            first = MOD._build_repeated_paired_eval(args, str(b1), str(c1))
            args.sampling_seed = 102
            second = MOD._build_repeated_paired_eval(args, str(b2), str(c2), first)
            self.assertEqual(first["replicate_count"], 1)
            self.assertEqual(second["replicate_count"], 2)
            self.assertEqual(second["pair_count"], 2)
            self.assertEqual([x["seed"] for x in second["replicates"]], [101, 102])
            self.assertTrue(MOD._paired_eval_has_replicates(second, 2))

    def test_full_opt_uses_configurable_budget_but_keeps_hard_protected_veto(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); round_dir = base / "round"; tune = base / "tune"
            round_dir.mkdir(); tune.mkdir()
            baseline = base / "baseline.json"; candidate = base / "candidate.json"
            write_report(baseline, {"task_a": 0.8, "task_b": 0.8, "task_c": 0.5})
            write_report(candidate, {"task_a": 0.69, "task_b": 0.69, "task_c": 0.8})
            state_path = round_dir / "round_state.json"
            state = {"candidate_opt_gate": {"valid": True}, "baseline_optimization": {"result_path": str(baseline), "task_scores": {"task_a": 0.8, "task_b": 0.8, "task_c": 0.5}}, "candidate_opt_signal_plan": {"protected_signals": []}, "bench": {"candidate_optimization_full": {"status": "succeeded", "resultPath": str(candidate), "summary": {"score": 0.72}}}}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            args = Namespace(bench_mode="local", protected_max_drop=0.1, full_opt_max_regressed_count=2, full_opt_max_regressed_ratio=1.0, full_opt_max_negative_delta=1.0, full_opt_max_single_drop=0.2, full_opt_min_mean_delta=-0.1)
            paths = {"round_dir": round_dir, "tune_dir": tune}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "action_bench_local", return_value={"status": "succeeded"}), mock.patch.object(MOD, "_evaluation_identity", return_value={}), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_bench_candidate_opt_full(args)
            gate = json.loads(state_path.read_text())["full_opt_gate"]
            self.assertTrue(gate["valid"], gate)
            self.assertEqual(gate["regression_count"], 2)

            state = json.loads(state_path.read_text())
            state["candidate_opt_signal_plan"] = {"protected_signals": [{"behavior_id": "PB-a", "task_id": "task_a", "metric": "score", "baseline": 0.8, "max_drop": 0.0}]}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), mock.patch.object(MOD, "action_bench_local", return_value={"status": "succeeded"}), mock.patch.object(MOD, "_evaluation_identity", return_value={}), mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_bench_candidate_opt_full(args)
            protected_gate = json.loads(state_path.read_text())["full_opt_gate"]
            self.assertFalse(protected_gate["valid"])
            self.assertEqual(len(protected_gate["hard_protected_regressions"]), 1)

    def test_research_diversity_warning_does_not_make_manifest_unsafe(self):
        manifest = valid_manifest()
        manifest["proposals"][1]["selected_operator"] = manifest["proposals"][0]["selected_operator"]
        manifest["proposals"][2]["selected_operator"] = "ADD_RESULT_VERIFIER"
        summary = {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/demo/SKILL.md"]}
        report = MOD._validate_change_manifest_quality(manifest, summary, search_contract={"required_operator_diversity": 3, "required_operator_family_diversity": 3})
        self.assertTrue(report["valid"], report["errors"])
        self.assertFalse(report["research_quality"]["passed"])


class CandidateMetricSchemaDriftTests(unittest.TestCase):
    def test_risk_judge_suffix_drift_resolves_semantically(self):
        with tempfile.TemporaryDirectory() as root:
            report = Path(root) / "report.json"
            report.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": 0.8, "breakdown": {"llm_judge.风险识别准确度": 0.75}}]}}]}), encoding="utf-8")
            result = MOD._extract_candidate_metric(report, "t", "llm_judge.风险识别准确度（含原文证据）")
            self.assertTrue(result["observable"], result)
            self.assertEqual(result["value"], 0.75)
            self.assertEqual(result["evidence"]["matched_request"], "llm_judge.风险识别准确度")

    def test_output_contract_abstract_metric_uses_structured_judge_field(self):
        with tempfile.TemporaryDirectory() as root:
            report = Path(root) / "report.json"
            report.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": 0.8, "breakdown": {"llm_judge.输出格式合规": 1.0}}]}}]}), encoding="utf-8")
            result = MOD._extract_candidate_metric(report, "t", "output_contract_complete")
            self.assertTrue(result["observable"], result)
            self.assertEqual(result["value"], 1.0)
            self.assertEqual(result["source"], "breakdown")

    def test_signal_compiler_corrects_declared_baseline_and_rejects_impossible_target(self):
        with tempfile.TemporaryDirectory() as root:
            report = Path(root) / "baseline.json"
            report.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": 0.8, "breakdown": {"llm_judge.输出格式合规": 1.0}}]}}]}), encoding="utf-8")
            expected = [{"task_id": "t", "metric": "llm_judge.输出格式合规", "baseline": 0.5, "direction": "increase", "min_delta": 0.3, "expected_min": 0.8, "role": "required"}]
            errors, warnings = MOD._compile_signal_baselines(report, expected, [])
            self.assertEqual(expected[0]["baseline"], 1.0)
            self.assertTrue(any("corrected" in warning for warning in warnings), warnings)
            self.assertTrue(any("outside bounded metric range" in error for error in errors), errors)

    def test_signal_compiler_rejects_unobservable_hard_protected_before_bench(self):
        with tempfile.TemporaryDirectory() as root:
            report = Path(root) / "baseline.json"
            report.write_text(json.dumps({"tasks": [{"task_id": "t", "grading": {"runs": [{"score": 0.8, "breakdown": {}}]}}]}), encoding="utf-8")
            protected = [{"behavior_id": "PB-x", "task_id": "t", "metric": "missing.metric", "gate": "hard", "min_value": 1, "max_drop": 0}]
            errors, _warnings = MOD._compile_signal_baselines(report, [], protected)
            self.assertTrue(any("not observable" in error for error in errors), errors)


class CandidatePartialEffectTests(unittest.TestCase):
    def _gate(self, candidate_score):
        with tempfile.TemporaryDirectory() as root:
            b = Path(root) / "b.json"; c = Path(root) / "c.json"
            write_report(b, {"t": 0.755}); write_report(c, {"t": candidate_score})
            plan = {"valid": True, "plan_id": "p", "schema_version": "evolution.candidate_opt_signal_plan.v2", "expected_signals": [{"task_id": "t", "metric": "score", "baseline": 0.755, "direction": "increase", "min_delta": 0.05, "role": "required"}], "protected_signals": []}
            state = {"baseline_optimization": {"result_path": str(b), "task_scores": {"t": 0.755}}, "bench": {"candidate_optimization_targeted": {"status": "succeeded", "resultPath": str(c), "startedAt": 20, "signalPlanId": "p"}}, "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}}}
            return MOD._candidate_opt_effect_report(state, plan)

    def test_positive_score_activation_below_tune_target_is_partial_pass(self):
        gate = self._gate(0.7809)
        self.assertTrue(gate["valid"], gate)
        self.assertEqual(gate["decision"], "effect_partial")
        self.assertTrue(gate["effect_gate_passed"])
        self.assertFalse(gate["effect_target_attained"])
        self.assertTrue(gate["required_expected_results"][0]["partial_pass"])

    def test_score_movement_below_activation_floor_is_rejected(self):
        gate = self._gate(0.760)
        self.assertFalse(gate["valid"], gate)
        self.assertEqual(gate["decision"], "effect_rejected")


if __name__ == "__main__":
    unittest.main()
