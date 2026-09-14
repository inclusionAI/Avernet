import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_promotion_state", WORKFLOW)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


class PromotionStateTest(unittest.TestCase):
    def test_old_acceptance_shape_is_not_inferred(self):
        with tempfile.TemporaryDirectory() as root:
            accept_dir = Path(root) / "acceptance"
            accept_dir.mkdir()
            (accept_dir / "acceptance_report.json").write_text(json.dumps({
                "accepted": True,
                "decision": "accepted_repeated_paired",
            }))
            with self.assertRaisesRegex(SystemExit, "bench_decision"):
                MOD._require_acceptance_report({"accept_dir": accept_dir})

    def test_tune_outputs_cannot_bypass_missing_round_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            round_dir = Path(root) / "round-001"
            tune_dir = round_dir / "tune"
            tune_dir.mkdir(parents=True)
            for name in ("tune_report.md", "changed_files.txt", "diff.patch", "change_manifest.json"):
                (tune_dir / name).write_text("ready")
            (round_dir / "round_state.json").write_text("{}")
            paths = {"round_dir": round_dir, "tune_dir": tune_dir}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths):
                with self.assertRaisesRegex(SystemExit, "rollback Pack"):
                    MOD.action_ensure_tune(Namespace())

    def test_round_sequence_reviews_before_publish_and_restores_after_publish(self):
        args = Namespace(skip_oss=False, skip_clawweb=True)
        names = [name for name, _ in MOD._round_sequence(args)]
        self.assertLess(names.index("pack"), names.index("ensure-review"))
        self.assertLess(names.index("ensure-review"), names.index("upload-oss"))
        self.assertLess(names.index("upload-oss"), names.index("restore"))
        self.assertLess(names.index("restore"), names.index("complete"))

    def test_complete_commits_accepted_only_after_pack_and_publish(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            run_dir = root / "run"
            round_dir = run_dir / "optimize/output/round-001"
            for child in ("acceptance", "artifacts", "spec", "upload"):
                (round_dir / child).mkdir(parents=True, exist_ok=True)

            artifact = round_dir / "artifacts/artifact_v1.zip"
            artifact.write_bytes(b"candidate")
            (round_dir / "artifacts/pack_report.json").write_text(json.dumps({
                "status": "success",
                "artifactPath": str(artifact),
                "artifactKind": "accepted",
                "sha256": MOD._sha256(artifact),
            }))
            (round_dir / "acceptance/acceptance_report.json").write_text(json.dumps({
                "schema_version": "evolution.acceptance.bench_review.v1",
                "bench_decision": "passed",
                "decision": "passed",
                "accepted": False,
                "promotion_status": "pending",
                "restore_required": False,
                "score": {"baseline": 0.5, "candidate": 0.8, "delta": 0.3},
            }))
            (round_dir / "upload/oss_manifest.json").write_text(json.dumps({
                "status": "SUCCESS",
                "manifestRef": "oss://pending-manifest",
                "manifest": {
                    "decision": {
                        "accepted": False,
                        "benchDecision": "passed",
                        "promotionStatus": "pending",
                    },
                    "objects": {"artifact": {"ref": "oss://candidate"}},
                },
            }))
            (round_dir / "round_state.json").write_text(json.dumps({
                "candidate_mutation_state": "applied",
                "bench": {
                    "optimization": {
                        "resultPath": "", "summary": {"score": 0.7},
                        "benchRunId": "bench-train", "producerStepId": "STEP-T",
                        "domainId": "train-domain", "domainOwnerId": "owner-1",
                    },
                    "validation": {
                        "resultPath": "", "summary": {"score": 0.8},
                        "benchRunId": "bench-test", "producerStepId": "STEP-T",
                        "domainId": "test-domain", "domainOwnerId": "owner-1",
                    },
                },
            }))

            paths = {
                "task_id": "EV-T",
                "run_dir": run_dir,
                "round_dir": round_dir,
                "optimize_output_dir": run_dir / "optimize/output",
                "accept_dir": round_dir / "acceptance",
                "artifacts_dir": round_dir / "artifacts",
                "spec_dir": round_dir / "spec",
                "upload_dir": round_dir / "upload",
            }
            args = Namespace(
                round=1, step_id="STEP-T", owner_id="owner-1",
                train_bench_domain_id="train-domain", test_bench_domain_id="test-domain",
                artifact_path="", skip_oss=False,
                model="antchat/GLM-5.1", suite="all", judge="",
                bench_timeout=10, strict_bench=False,
            )
            artifact_client = mock.Mock()
            artifact_client.upload.return_value = {"ref": "oss://final-manifest"}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_append_experiment_ledger", return_value="ledger"), \
                 mock.patch.object(MOD, "_evaluation_identity", return_value={}), \
                 mock.patch.object(MOD, "_artifact_client", return_value=artifact_client), \
                 mock.patch.object(MOD, "_log"), mock.patch.object(MOD, "_print_json"):
                MOD.action_complete(args)

            acceptance = json.loads((round_dir / "acceptance/acceptance_report.json").read_text())
            state = json.loads((round_dir / "round_state.json").read_text())
            manifest = json.loads((run_dir / "optimize/output/optimize_manifest.json").read_text())
            self.assertTrue(acceptance["accepted"])
            self.assertEqual(acceptance["promotion_status"], "succeeded")
            self.assertTrue(state["accepted"])
            self.assertEqual(state["candidate_mutation_state"], "promoted")
            self.assertEqual(manifest["last_accepted_round"], 1)
            pack_report = json.loads((round_dir / "artifacts/pack_report.json").read_text())
            self.assertEqual(pack_report["artifactKind"], "accepted")
            self.assertEqual(pack_report["promotionStatus"], "succeeded")
            oss_manifest = json.loads((round_dir / "upload/oss_manifest.json").read_text())
            self.assertTrue(oss_manifest["manifest"]["decision"]["accepted"])
            self.assertEqual(
                oss_manifest["manifest"]["decision"]["promotionStatus"], "succeeded"
            )
            self.assertEqual(
                oss_manifest["manifest"]["effectiveArtifact"]["ref"], "oss://candidate"
            )
            self.assertEqual(oss_manifest["manifestRef"], "oss://final-manifest")
            artifact_client.upload.assert_called_once()

    def test_final_projection_upload_failure_does_not_revoke_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            round_dir = root / "round-001"
            upload_dir = round_dir / "upload"
            artifacts_dir = round_dir / "artifacts"
            upload_dir.mkdir(parents=True)
            artifacts_dir.mkdir()
            (artifacts_dir / "pack_report.json").write_text(json.dumps({
                "status": "success",
                "artifactKind": "accepted",
                "promotionStatus": "pending",
            }))
            (upload_dir / "oss_manifest.json").write_text(json.dumps({
                "status": "SUCCESS",
                "manifestRef": "oss://pending-manifest",
                "manifest": {
                    "decision": {"accepted": False, "promotionStatus": "pending"},
                    "objects": {"artifact": {"ref": "oss://candidate"}},
                },
            }))
            paths = {"round_dir": round_dir, "upload_dir": upload_dir}
            args = Namespace(round=1, skip_oss=False)
            client = mock.Mock()
            client.upload.side_effect = RuntimeError("temporary upload failure")

            with mock.patch.object(MOD, "_artifact_client", return_value=client), \
                 mock.patch.object(MOD, "_log"):
                result = MOD._finalize_accepted_round_projections(args, paths, {
                    "accepted": True,
                    "promotion_status": "succeeded",
                    "bench_decision": "passed",
                    "decision": "passed",
                })

            self.assertEqual(result["status"], "warning")
            oss_manifest = json.loads((upload_dir / "oss_manifest.json").read_text())
            self.assertTrue(oss_manifest["manifest"]["decision"]["accepted"])
            self.assertEqual(
                oss_manifest["manifest"]["decision"]["promotionStatus"], "succeeded"
            )
            self.assertIn("finalizationWarnings", oss_manifest)


if __name__ == "__main__":
    unittest.main()
