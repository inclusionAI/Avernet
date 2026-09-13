"""Fast integration tests for the post-candidate-gate ClawEvolve state machine.

No agent, benchmark, deploy, upload, or real pack process is executed.  Reports and
artifacts are synthetic, while gate/acceptance/replication/complete logic is real.
"""

import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_post_gate_target", HANDLER)
MOD = importlib.util.module_from_spec(SPEC)
import sys
sys.path.insert(0, str(HANDLER.parent))
SPEC.loader.exec_module(MOD)


def write_report(path: Path, scores: dict[str, float]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "model": "model-a", "benchmark_version": "1.2.1", "suite": "all",
        "tasks": [{"task_id": tid, "grading": {"runs": [{"score": score, "breakdown": {}}]}} for tid, score in scores.items()],
    }), encoding="utf-8")


class PostGatePipelineTests(unittest.TestCase):
    def _fixture(self, root: str):
        base = Path(root)
        workspace = base / "workspace"
        run_dir = workspace / "clawevolve_results/EV-MOCK"
        output = run_dir / "optimize/output"
        round_dir = output / "round-002"
        dirs = {
            "workspace": workspace, "run_dir": run_dir, "optimize_output_dir": output,
            "optimize_input_dir": run_dir / "optimize/input", "round_dir": round_dir,
            "input_dir": round_dir / "input", "tune_dir": round_dir / "tune",
            "accept_dir": round_dir / "acceptance", "artifacts_dir": round_dir / "artifacts",
            "spec_dir": round_dir / "spec", "upload_dir": round_dir / "upload",
            "skill_base": ROOT, "task_id": "EV-MOCK",
        }
        for key, value in dirs.items():
            if isinstance(value, Path) and key not in {"workspace", "run_dir", "optimize_output_dir", "skill_base"}:
                value.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        identity = {
            "bench_model": "model-a", "benchmark_version": "1.2.1",
            "bench_mode": "local", "suite": "all",
            "validation_fixture": {"sha256": "fixture-v"},
        }
        baseline_artifact = output / "round-001/artifacts/artifact_v1.zip"
        baseline_artifact.parent.mkdir(parents=True, exist_ok=True)
        baseline_artifact.write_bytes(b"baseline")
        full_report = round_dir / "bench/full.json"
        validation_report = round_dir / "bench/validation.json"
        write_report(full_report, {"opt_a": 0.8, "opt_b": 0.8})
        write_report(validation_report, {"val_a": 0.9, "val_b": 0.8})
        state = {
            "identity": identity,
            "baseline_artifact": str(baseline_artifact),
            "change_summary": {"is_noop": False, "source": "system_workspace_diff", "trusted": True, "touched_paths": ["skills/skills-local/demo/SKILL.md"]},
            "change_manifest": {"changes": [], "selected_proposal_id": "P-1"},
            "reachability": {"activated": True}, "skill_creation": {"valid": True},
            "candidate_gate": {"valid": True, "is_noop": False, "reasons": []},
            "candidate_opt_gate": {"valid": True, "decision": "effect_passed", "effect_gate_passed": True, "protected_gate_passed": True, "reasons": []},
            "full_opt_gate": {"valid": True, "major_regressions": [], "hard_protected_regressions": [], "missing_task_ids": []},
            "bench": {
                "candidate_optimization_full": {"status": "succeeded", "resultPath": str(full_report), "summary": {"score": 0.8}},
                "optimization": {"status": "succeeded", "resultPath": str(full_report), "summary": {"score": 0.8}},
                "validation": {"status": "succeeded", "resultPath": str(validation_report), "summary": {"score": 0.85, "model": "model-a", "benchmark_version": "1.2.1"}},
            },
        }
        (round_dir / "round_state.json").write_text(json.dumps(state), encoding="utf-8")
        (dirs["tune_dir"] / "tune_report.md").write_text("mock tune", encoding="utf-8")
        (dirs["spec_dir"] / "spec-v2.md").write_text("mock spec", encoding="utf-8")
        (dirs["spec_dir"] / "spec-v2.json").write_text("{}", encoding="utf-8")
        manifest = {
            "schema_version": "evolution.run_manifest.v0", "task_id": "EV-MOCK", "evolve_run_id": "EV-MOCK", "rounds": [],
            "last_accepted_round": 1, "last_accepted_validation_score": 0.6,
            "last_accepted_validation_task_scores": {"val_a": 0.6, "val_b": 0.6},
            "last_accepted_identity": identity, "last_accepted_identity_complete": True,
            "last_accepted_artifact": {"localPath": str(baseline_artifact)},
        }
        (output / "optimize_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        paths = {k: (str(v) if k in {"workspace", "skill_base"} else v) for k, v in dirs.items()}
        args = Namespace(
            round=2, bench_mode="local", acceptance_margin=0.0, replication_band=0.03,
            paired_min_runs=2, paired_min_win_rate=0.5, exploration_loss_budget=0.0,
            sampling_seed=100, model="model-a", suite="all", judge="", bench_timeout=10,
            strict_bench=False, max_artifact_mb=0, pack_include_evolve_results=False,
            pack_dry_run=True, artifact_path="", baseline_artifact="",
        )
        return paths, args, identity

    def test_success_path_selects_candidate_for_promotion(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, identity = self._fixture(root)
            round_dir = paths["round_dir"]
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            initial = json.loads((paths["accept_dir"] / "acceptance_report.json").read_text())
            self.assertEqual(initial["decision"], "passed")
            self.assertEqual(initial["promotion_status"], "pending")
            self.assertFalse(initial["accepted"])
            self.assertFalse(initial["restore_required"])

    def test_repeated_pair_builder_deduplicates_identical_report_pair(self):
        with tempfile.TemporaryDirectory() as root:
            baseline = Path(root) / "baseline.json"; candidate = Path(root) / "candidate.json"
            write_report(baseline, {"a": 0.5}); write_report(candidate, {"a": 0.8})
            args = Namespace(acceptance_margin=0.0, paired_min_win_rate=0.5, sampling_seed=1)
            first = MOD._build_repeated_paired_eval(args, str(baseline), str(candidate))
            second = MOD._build_repeated_paired_eval(args, str(baseline), str(candidate), first)
            self.assertEqual(second["replicate_count"], 1)
            self.assertEqual(second["pair_count"], 1)

    def test_positive_score_does_not_request_restore_before_promotion(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            args.acceptance_margin = 0.5  # current delta 0.25 is inside the near band
            args.paired_min_runs = 3
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_load_or_compute_paired_eval", return_value={"n": 0, "passed": None}), \
                 mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((paths["accept_dir"] / "acceptance_report.json").read_text())
            self.assertEqual(acceptance["decision"], "passed")
            self.assertEqual(acceptance["promotion_status"], "pending")
            self.assertFalse(acceptance["restore_required"], acceptance)

    def test_static_candidate_gate_is_advisory_by_default_but_noop_still_stops(self):
        advisory_args = Namespace(enforce_candidate_gate=False)
        strict_args = Namespace(enforce_candidate_gate=True)
        failed = {"valid": False, "is_noop": False, "reasons": ["diff provenance"]}
        noop = {"valid": False, "is_noop": True, "reasons": ["no candidate changes"]}

        self.assertTrue(MOD._candidate_gate_allows_progress(advisory_args, failed))
        self.assertFalse(MOD._candidate_gate_allows_progress(strict_args, failed))
        self.assertFalse(MOD._candidate_gate_allows_progress(advisory_args, noop))

    def test_validation_runs_when_static_candidate_gate_is_advisory(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            args.enforce_candidate_gate = False
            state_path = paths["round_dir"] / "round_state.json"
            state = json.loads(state_path.read_text())
            state["candidate_gate"] = {
                "valid": False, "is_noop": False,
                "reasons": ["candidate diff provenance validation failed"],
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "action_bench_local", return_value={"status": "succeeded"}) as bench, \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), \
                 mock.patch.object(MOD, "_print_json"):
                MOD.action_bench_val(args)
            bench.assert_called_once()

    def test_acceptance_uses_completed_validation_when_static_gate_is_advisory(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            args.enforce_candidate_gate = False
            state_path = paths["round_dir"] / "round_state.json"
            state = json.loads(state_path.read_text())
            state["candidate_gate"] = {
                "valid": False, "is_noop": False,
                "reasons": ["candidate diff provenance validation failed"],
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((paths["accept_dir"] / "acceptance_report.json").read_text())
            self.assertNotEqual(acceptance["decision"], "invalid_candidate", acceptance)
            self.assertEqual(acceptance["decision"], "passed", acceptance)
            self.assertEqual(acceptance["promotion_status"], "pending")

    def test_validation_skip_reason_reports_actual_full_opt_budget_failure(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            state_path = paths["round_dir"] / "round_state.json"
            state = json.loads(state_path.read_text())
            state["full_opt_gate"] = {
                "valid": False, "major_regressions": [], "hard_protected_regressions": [],
                "missing_task_ids": [], "budget_failures": ["mean delta -0.04 < -0.02"],
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "action_bench_local") as bench, mock.patch.object(MOD, "_mark_step"), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_bench_val(args)
            bench.assert_called_once()
            result = json.loads(state_path.read_text())
            self.assertIn("mean delta -0.04 < -0.02", result["evaluation_advisories"]["full_opt"]["reasons"])

    def test_complete_refuses_accepted_round_without_pack_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, identity = self._fixture(root)
            acceptance = {
                "accepted": False, "bench_decision": "passed",
                "promotion_status": "pending", "decision": "passed", "restore_required": False,
            }
            (paths["accept_dir"] / "acceptance_report.json").write_text(json.dumps(acceptance), encoding="utf-8")
            for artifact in paths["artifacts_dir"].glob("*.zip"):
                artifact.unlink()
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_evaluation_identity", return_value=identity), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                with self.assertRaisesRegex(SystemExit, "artifact"):
                    MOD.action_complete(args)

    def test_full_opt_to_validation_handoff_runs_candidate_not_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            state_path = paths["round_dir"] / "round_state.json"
            state = json.loads(state_path.read_text())
            baseline_opt = paths["round_dir"] / "bench/baseline-opt.json"
            candidate_opt = paths["round_dir"] / "bench/candidate-full.json"
            candidate_val = paths["round_dir"] / "bench/candidate-val.json"
            write_report(baseline_opt, {"opt_a": 0.6, "opt_b": 0.6})
            write_report(candidate_opt, {"opt_a": 0.8, "opt_b": 0.75})
            write_report(candidate_val, {"val_a": 0.8, "val_b": 0.8})
            state["baseline_optimization"] = {"result_path": str(baseline_opt), "task_scores": {"opt_a": 0.6, "opt_b": 0.6}}
            state["candidate_opt_signal_plan"] = {"schema_version": "evolution.candidate_opt_signal_plan.v2", "protected_signals": []}
            state["bench"].pop("candidate_optimization_full", None)
            state["bench"].pop("optimization", None)
            state["bench"].pop("validation", None)
            state.pop("full_opt_gate", None)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            calls = []
            def fake_bench(_args, kind, **kwargs):
                calls.append((kind, kwargs.get("state_key"), kwargs.get("step_name_override")))
                current = json.loads(state_path.read_text())
                key = kwargs.get("state_key") or kind
                report = candidate_opt if kind == "optimization" else candidate_val
                current.setdefault("bench", {})[key] = {"status": "succeeded", "exitCode": 0, "resultPath": str(report), "summary": {"score": 0.775 if kind == "optimization" else 0.8}}
                state_path.write_text(json.dumps(current), encoding="utf-8")
                return {"status": "succeeded", "resultPath": str(report)}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "action_bench_local", side_effect=fake_bench), \
                 mock.patch.object(MOD, "_evaluation_identity", return_value={}), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_bench_candidate_opt_full(args)
                full_state = json.loads(state_path.read_text())
                self.assertTrue(full_state["full_opt_gate"]["valid"], full_state["full_opt_gate"])
                MOD.action_bench_val(args)
            final = json.loads(state_path.read_text())
            self.assertEqual(final["bench"]["optimization"]["resultPath"], str(candidate_opt))
            self.assertEqual(final["bench"]["validation"]["resultPath"], str(candidate_val))
            self.assertEqual(calls[0][0], "optimization")
            self.assertEqual(calls[1][0], "validation")


    def test_observation_first_runs_full_opt_after_effect_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            state_path = paths["round_dir"] / "round_state.json"
            state = json.loads(state_path.read_text())
            state["candidate_opt_gate"] = {"valid": False, "decision": "effect_rejected", "reasons": ["no targeted effect"]}
            state["candidate_gate"] = {"valid": True, "reasons": []}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "action_bench_local", return_value={"status": "succeeded"}) as bench, \
                 mock.patch.object(MOD, "_evaluation_identity", return_value={}), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_bench_candidate_opt_full(args)
            bench.assert_called_once()
            final = json.loads(state_path.read_text())
            self.assertEqual(final["bench"]["candidate_optimization_full"]["upstream_candidate_opt_advisory"]["decision"], "effect_rejected")

    def test_observation_first_acceptance_uses_validation_despite_diagnostic_failures(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            state_path = paths["round_dir"] / "round_state.json"
            state = json.loads(state_path.read_text())
            state["candidate_opt_gate"] = {"valid": False, "decision": "effect_rejected", "reasons": ["targeted gain weak"]}
            state["full_opt_gate"] = {"valid": False, "budget_failures": ["mean delta below exploratory budget"], "hard_protected_regressions": [], "missing_task_ids": []}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((paths["accept_dir"] / "acceptance_report.json").read_text())
            self.assertEqual(acceptance["decision"], "passed", acceptance)
            self.assertEqual(acceptance["promotion_status"], "pending")
            self.assertFalse(acceptance["restore_required"])
            self.assertNotIn("evaluation_advisories", acceptance)

    def test_hard_protected_regression_is_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as root:
            paths, args, _identity = self._fixture(root)
            state_path = paths["round_dir"] / "round_state.json"
            state = json.loads(state_path.read_text())
            state["full_opt_gate"] = {"valid": False, "budget_failures": [], "hard_protected_regressions": [{"behavior_id": "PB-safety"}], "missing_task_ids": []}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_write_evidence_database"), mock.patch.object(MOD, "_mark_step"), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_accept(args)
            acceptance = json.loads((paths["accept_dir"] / "acceptance_report.json").read_text())
            self.assertEqual(acceptance["decision"], "passed")
            self.assertEqual(acceptance["promotion_status"], "pending")
            self.assertFalse(acceptance["restore_required"])


if __name__ == "__main__":
    unittest.main()
