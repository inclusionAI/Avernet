import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_clawevolve_optimize_run_bench_compat import handler


class InitialBaselineCacheTests(unittest.TestCase):
    def _load(self, *, last_accepted, cached_round, desired_hash="frozen"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "optimize/output"
            round_dir = output / "round-002"
            round_dir.mkdir(parents=True)
            artifact = root / "artifact.zip"
            artifact.write_bytes(b"baseline")
            report = root / "report.json"
            report.write_text("{}")
            registries = {
                role: {
                    "available": True,
                    "round_id": cached_round,
                    "result_path": str(report),
                    "summary": {"score": score},
                    "identity": {"identity_sha256": "frozen", "cache_complete": True},
                }
                for role, score in [("optimization", 0.58375), ("validation", 0.0)]
            }
            manifest = {
                f"accepted_baseline_{role}": row for role, row in registries.items()
            }
            if last_accepted != "missing":
                manifest["last_accepted_round"] = last_accepted
            manifest_path = output / "optimize_manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            (round_dir / "round_state.json").write_text(json.dumps({
                "baseline_artifact": str(artifact),
            }))
            paths = {"optimize_output_dir": output, "round_dir": round_dir, "task_id": "EV-1"}

            def rerun(args, paths, *, kind, artifact_path, round_id):
                return {**registries[kind], "round_id": round_id}

            with patch.object(handler, "resolve_paths", return_value=paths), patch.object(
                handler, "_evaluation_identity", return_value={
                    "identity_sha256": desired_hash, "cache_complete": True,
                }
            ), patch.object(handler, "_run_and_register_baseline_role", side_effect=rerun) as run, patch.object(
                handler, "_mark_step"
            ) as mark, patch.object(handler, "_print_json"):
                handler.action_load_baseline_opt(argparse.Namespace(round=2))
            return run.call_args_list, mark.call_args.args[3], json.loads(manifest_path.read_text())

    def test_rejected_first_round_reuses_initial_train_and_test(self):
        for last_accepted in ("missing", None, 0):
            with self.subTest(last_accepted=last_accepted):
                calls, result, manifest = self._load(last_accepted=last_accepted, cached_round=0)
                self.assertEqual(calls, [])
                self.assertEqual(result["trainCacheStatus"], "registry_hit")
                self.assertEqual(result["testCacheStatus"], "registry_hit")
                self.assertEqual(result["trainScore"], 0.58375)
                self.assertEqual(result["testScore"], 0.0)
                self.assertEqual(manifest["baseline_registry"]["round_id"], 0)

    def test_accepted_round_reuses_promoted_baseline(self):
        calls, result, manifest = self._load(last_accepted=1, cached_round=1)
        self.assertEqual(calls, [])
        self.assertEqual(result["trainCacheStatus"], "registry_hit")
        self.assertEqual(result["testCacheStatus"], "registry_hit")
        self.assertEqual(manifest["baseline_registry"]["round_id"], 1)

    def test_changed_evaluation_identity_still_recomputes_baselines(self):
        calls, result, _ = self._load(last_accepted=None, cached_round=0, desired_hash="changed")
        self.assertEqual(len(calls), 2)
        self.assertEqual({call.kwargs["kind"] for call in calls}, {"optimization", "validation"})
        self.assertTrue(all(call.kwargs["round_id"] == 0 for call in calls))
        self.assertEqual(result["trainCacheStatus"], "generated")
        self.assertEqual(result["testCacheStatus"], "generated")

    def test_accepted_round_does_not_reuse_previous_artifact_scores(self):
        calls, result, _ = self._load(last_accepted=1, cached_round=0)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call.kwargs["round_id"] == 1 for call in calls))
        self.assertEqual(result["trainCacheStatus"], "generated")
        self.assertEqual(result["testCacheStatus"], "generated")
