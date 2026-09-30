import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/replay_candidate_gate.py"
SPEC = importlib.util.spec_from_file_location("replay_candidate_gate", SCRIPT)
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)
HANDLER = REPLAY._load_handler()


class CandidateGateReplayTests(unittest.TestCase):
    def _report(self, path, base, fmt):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tasks": [{
            "task_id": "task_a",
            "grading": {"runs": [{"score": base, "breakdown": {"llm_judge.输出格式合规": fmt}}]},
        }]}), encoding="utf-8")

    def test_replay_resolves_remote_paths_and_flat_breakdown_metrics(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root) / "EV-test"
            round_dir = run / "optimize/output/round-001"
            baseline = run / "bench/baseline/x/train/output/benchmark/run/baseline_report.json"
            candidate = round_dir / "bench/candidate_optimization_targeted/output/benchmark/run/candidate_report.json"
            self._report(baseline, 0.2, 0)
            self._report(candidate, 0.8, 1)
            plan = {
                "valid": True,
                "plan_id": "p1",
                "expected_signals": [{"task_id": "task_a", "metric": "llm_judge.输出格式合规", "direction": "boolean_flip", "expected_value": 1, "role": "required"}],
                "protected_signals": [{"task_id": "task_a", "metric": "score", "baseline": 0.2, "max_drop": 0.1}],
            }
            state = {
                "candidate_gate": {"valid": True, "reasons": []},
                "candidate_opt_signal_plan": plan,
                "baseline_optimization": {"available": True, "result_path": "/remote/baseline_report.json", "task_scores": {"task_a": 0.2}},
                "bench": {"candidate_optimization_targeted": {"status": "succeeded", "resultPath": "/remote/candidate_report.json", "summary": {"score": 0.8}, "startedAt": 20}},
                "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}},
                "candidate_opt_gate": {"valid": False, "decision": "effect_rejected"},
            }
            round_dir.mkdir(parents=True, exist_ok=True)
            (round_dir / "round_state.json").write_text(json.dumps(state), encoding="utf-8")
            result = REPLAY.replay_round(HANDLER, run, 1)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["unobservable_count"], 0, result)
            self.assertTrue(result["replayed_gate"]["valid"], result)
            self.assertIn("recorded and replayed candidate gate decisions differ under current code", result["anomalies"])

    def test_replay_flags_valid_plan_with_unobservable_metric(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root) / "EV-test"
            round_dir = run / "optimize/output/round-001"
            baseline = run / "baseline_report.json"
            candidate = round_dir / "candidate_report.json"
            self._report(baseline, 0.2, 0)
            self._report(candidate, 0.8, 1)
            state = {
                "candidate_gate": {"valid": True},
                "candidate_opt_signal_plan": {"valid": True, "expected_signals": [{"task_id": "task_a", "metric": "missing.metric", "min_delta": 1}], "protected_signals": [{"task_id": "task_a", "metric": "score", "max_drop": 0.1}]},
                "baseline_optimization": {"result_path": str(baseline), "task_scores": {"task_a": 0.2}},
                "bench": {"candidate_optimization_targeted": {"status": "succeeded", "resultPath": str(candidate), "startedAt": 20}},
                "steps": {"ensure-tune": {"updated_at": "1970-01-01T00:00:10+00:00"}},
            }
            round_dir.mkdir(parents=True, exist_ok=True)
            (round_dir / "round_state.json").write_text(json.dumps(state), encoding="utf-8")
            result = REPLAY.replay_round(HANDLER, run, 1)
            self.assertEqual(result["unobservable_count"], 1)
            self.assertIn("valid signal plan references metrics that the gate cannot observe", result["anomalies"])


if __name__ == "__main__":
    unittest.main()
