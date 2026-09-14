import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PATH = Path(__file__).parents[1] / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("optimize_run_baseline_test", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class BaselinePackRetryTest(unittest.TestCase):
    @staticmethod
    def _write_zip(path: Path, content: bytes = b"snapshot") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.yaml", "schemaVersion: 3\n")
            archive.writestr("package/AGENTS.md", content)

    def test_initial_pack_is_registered_before_tune(self):
        args = SimpleNamespace(
            clawweb_url="https://clawweb.example.com",
            clawweb_url_camel="",
            skip_clawweb=False,
        )
        published = {
            "kind": "baseline_pack",
            "ref": "oss://clawevolve-artifacts/evolution/EV-T/baseline/artifact_v0.zip",
            "size": 12,
            "sha256": "a" * 64,
            "contentType": "application/zip",
        }
        with mock.patch.object(
            MOD,
            "_post_clawweb_payload_best_effort",
            return_value={"status": "SUCCESS"},
        ) as post:
            result = MOD._register_initial_pack_with_clawweb(args, published)

        self.assertEqual(result["status"], "SUCCESS")
        payload = post.call_args.args[1]
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["output"]["baselineArtifact"]["artifact"], published)

    def test_initial_pack_registration_failure_blocks_tune(self):
        args = SimpleNamespace(
            clawweb_url="https://clawweb.example.com",
            clawweb_url_camel="",
            skip_clawweb=False,
        )
        published = {
            "kind": "baseline_pack",
            "ref": "oss://clawevolve-artifacts/evolution/EV-T/baseline/artifact_v0.zip",
        }
        with mock.patch.object(
            MOD,
            "_post_clawweb_payload_best_effort",
            return_value={"status": "UPLOAD_FAILED", "stderr": "HTTP 500"},
        ):
            with self.assertRaisesRegex(SystemExit, "registration failed"):
                MOD._register_initial_pack_with_clawweb(args, published)

    def test_artifact_upload_failure_keeps_reusable_pending_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            baseline_dir = root_path / "input/baseline"
            round_dir = root_path / "round-001"
            round_dir.mkdir(parents=True)
            paths = {
                "task_id": "EV-T",
                "optimize_input_dir": root_path / "input",
                "round_dir": round_dir,
            }
            args = type("Args", (), {"round": 1, "step_id": "STEP-S", "restore_precheck": False})()

            def create_artifact(_args, target, **_kwargs):
                self._write_zip(target, b"immutable-pack")
                return {
                    "kind": "baseline_pack",
                    "path": str(target),
                    "size": target.stat().st_size,
                    "sha256": MOD._sha256(target),
                    "contentType": "application/zip",
                    "createdAt": "now",
                }

            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_create_pack_artifact", side_effect=create_artifact) as create, \
                 mock.patch.object(MOD, "_artifact_client") as client_factory:
                client_factory.return_value.upload.side_effect = RuntimeError("upload failed")
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    MOD.action_baseline_pack(args)

            pending = json.loads((baseline_dir / "baseline-manifest.json").read_text())
            self.assertEqual(pending["publishStatus"], "PENDING")
            self.assertFalse(pending["artifactUploaded"])

            uploads = []
            def upload(kind, path, content_type, _round=None):
                uploads.append((kind, Path(path).name))
                return {"kind": kind, "ref": f"oss://clawevolve-artifacts/evolution/EV-T/{Path(path).name}",
                        "size": Path(path).stat().st_size, "sha256": MOD._sha256(Path(path)), "contentType": content_type}
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_create_pack_artifact") as create_again, \
                 mock.patch.object(MOD, "_artifact_client") as client_factory, \
                 mock.patch.object(MOD, "_mark_step"), \
                 mock.patch.object(MOD, "_print_json"):
                client_factory.return_value.upload.side_effect = upload
                MOD.action_baseline_pack(args)

            create_again.assert_not_called()
            self.assertEqual(len(uploads), 2)
            success = json.loads((baseline_dir / "baseline-manifest.json").read_text())
            self.assertEqual(success["publishStatus"], "SUCCESS")
            rollback = round_dir / "rollback/artifact_before_tune.zip"
            rollback_manifest = json.loads((round_dir / "rollback/rollback-manifest.json").read_text())
            state = json.loads((round_dir / "round_state.json").read_text())
            self.assertTrue(rollback.is_file())
            self.assertEqual(rollback.read_bytes(), (baseline_dir / "artifact_v0.zip").read_bytes())
            self.assertNotEqual(str(rollback), str(baseline_dir / "artifact_v0.zip"))
            self.assertEqual(rollback_manifest["status"], "SUCCESS")
            self.assertEqual(state["baseline_artifact"], str(baseline_dir / "artifact_v0.zip"))
            self.assertEqual(state["rollback_snapshot"]["path"], str(rollback))

    def test_watchdog_does_not_skip_pending_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root); baseline = root_path / "input/baseline"; baseline.mkdir(parents=True)
            artifact = baseline / "artifact_v0.zip"; self._write_zip(artifact, b"pack")
            (baseline / "baseline-manifest.json").write_text(json.dumps({"publishStatus":"PENDING","artifactUploaded":False,"manifestUploaded":False,"sha256":MOD._sha256(artifact)}))
            paths = {"task_id":"EV-T","optimize_input_dir":root_path / "input","round_dir":root_path / "round-001","upload_dir":root_path / "round-001/upload"}
            args = type("Args", (), {"round":1,"step_id":"STEP-S","resume":True,"force":False})()
            def finish(_args):
                rollback = paths["round_dir"] / "rollback/artifact_before_tune.zip"
                self._write_zip(rollback, b"pack")
                initial_digest = MOD._sha256(artifact)
                rollback_digest = MOD._sha256(rollback)
                (baseline / "baseline-manifest.json").write_text(json.dumps({"publishStatus":"SUCCESS","artifactUploaded":True,"manifestUploaded":True,"sha256":initial_digest}))
                (rollback.parent / "rollback-manifest.json").write_text(json.dumps({"status":"SUCCESS","sha256":rollback_digest}))
                (paths["round_dir"] / "round_state.json").parent.mkdir(parents=True, exist_ok=True)
                (paths["round_dir"] / "round_state.json").write_text(json.dumps({"rollback_snapshot":{"status":"ready","path":str(rollback),"sha256":rollback_digest}}))
            action = mock.Mock(side_effect=finish)
            with mock.patch.object(MOD,"resolve_paths",return_value=paths), mock.patch.object(MOD,"_mark_step"), mock.patch.object(MOD,"_log"), mock.patch.object(MOD,"_step_policy",return_value={"max_attempts":1}):
                result = MOD._call_action_for_round_watchdog(args,"baseline-pack",action)
            action.assert_called_once_with(args); self.assertEqual(result["status"],"SUCCESS")

    def test_round_two_creates_its_own_immutable_rollback_pack(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            round_dir = root_path / "round-002"
            rollback_dir = round_dir / "rollback"
            round_dir.mkdir(parents=True)
            accepted = root_path / "round-001/artifacts/artifact_v1.zip"
            self._write_zip(accepted, b"accepted")
            (round_dir / "round_state.json").write_text(json.dumps({"baseline_artifact": str(accepted)}))
            paths = {
                "task_id": "EV-T", "optimize_input_dir": root_path / "input",
                "round_dir": round_dir, "rollback_dir": rollback_dir,
            }
            args = type("Args", (), {"round": 2, "step_id": "STEP-R2", "restore_precheck": False})()

            def create_artifact(_args, target, **_kwargs):
                self._write_zip(target, b"round-two-live")
                return {
                    "kind": "round_rollback_pack", "path": str(target),
                    "size": target.stat().st_size, "sha256": MOD._sha256(target),
                    "contentType": "application/zip", "createdAt": "now",
                }

            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_create_pack_artifact", side_effect=create_artifact), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_print_json"):
                MOD.action_baseline_pack(args)

            target = rollback_dir / "artifact_before_tune.zip"
            state = json.loads((round_dir / "round_state.json").read_text())
            self.assertEqual(state["baseline_artifact"], str(accepted))
            self.assertEqual(state["rollback_snapshot"]["path"], str(target))
            self.assertNotEqual(state["rollback_snapshot"]["path"], state["baseline_artifact"])
            self.assertEqual(json.loads((rollback_dir / "rollback-manifest.json").read_text())["status"], "SUCCESS")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths):
                self.assertTrue(MOD._step_done(args, "baseline-pack"))

    def test_reject_restore_uses_current_round_snapshot_not_accepted_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            round_dir = root_path / "round-002"
            accept_dir = round_dir / "acceptance"
            accept_dir.mkdir(parents=True)
            rollback = round_dir / "rollback/artifact_before_tune.zip"
            accepted = root_path / "round-001/artifacts/artifact_v1.zip"
            self._write_zip(rollback, b"round-live-before-tune")
            self._write_zip(accepted, b"older-accepted")
            (accept_dir / "acceptance_report.json").write_text(json.dumps({
                "accepted": False,
                "bench_decision": "not_improved",
                "promotion_status": "not_started",
                "decision": "not_improved",
                "restore_required": True,
            }))
            (round_dir / "round_state.json").write_text(json.dumps({
                "baseline_artifact": str(accepted),
                "candidate_mutation_state": "applied",
                "rollback_snapshot": {
                    "status": "ready", "path": str(rollback), "sha256": MOD._sha256(rollback),
                },
            }))
            paths = {
                "task_id": "EV-T", "round_dir": round_dir, "accept_dir": accept_dir,
                "skill_base": str(root_path / "skills"), "workspace": str(root_path / "workspace"),
                "optimize_output_dir": root_path,
            }
            args = SimpleNamespace(
                round=2, baseline_artifact="", restore_precheck=False,
                workspace=str(root_path / "workspace"), task_id="EV-T",
            )
            commands = []
            def run(cmd, **_kwargs):
                commands.append(cmd)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "find_script", return_value=root_path / "deploy.sh"), \
                 mock.patch.object(MOD, "_assert_restore_runtime_compatible", return_value={"compatible": True}), \
                 mock.patch.object(MOD.subprocess, "run", side_effect=run), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), \
                 mock.patch.object(MOD, "_print_json"):
                MOD.action_restore(args)

            image_index = commands[0].index("--image") + 1
            self.assertEqual(commands[0][image_index], str(rollback))
            self.assertNotEqual(commands[0][image_index], str(accepted))
            state = json.loads((round_dir / "round_state.json").read_text())
            self.assertEqual(state["candidate_mutation_state"], "restored")

    def test_fallback_reject_uses_mutation_state_for_restore_requirement(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            round_dir = root_path / "round-002"
            accept_dir = round_dir / "acceptance"
            accept_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({
                "candidate_mutation_state": "possibly_applied",
                "rollback_snapshot": {
                    "status": "ready",
                    "path": str(round_dir / "rollback/artifact_before_tune.zip"),
                    "sha256": "sha",
                },
                "bench": {},
            }))
            paths = {
                "task_id": "EV-T", "round_dir": round_dir, "accept_dir": accept_dir,
                "run_dir": root_path, "optimize_input_dir": root_path / "input",
            }
            args = SimpleNamespace(round=2)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths):
                result = MOD._fallback_reject_round(args, {"message": "bench failed"})

            self.assertTrue(result["restore_required"])
            report = json.loads((accept_dir / "acceptance_report.json").read_text())
            self.assertTrue(report["restore_required"])

    def test_new_step_recovery_restores_before_round_archive(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            round_dir = root_path / "round-002"
            round_dir.mkdir(parents=True)
            rollback = round_dir / "rollback/artifact_before_tune.zip"
            self._write_zip(rollback)
            state = {
                "step_id": "STEP-OLD",
                "status": "ROUND_STARTED",
                "candidate_mutation_state": "applied",
                "rollback_snapshot": {
                    "status": "ready", "path": str(rollback), "sha256": MOD._sha256(rollback),
                },
            }
            (round_dir / "round_state.json").write_text(json.dumps(state))
            paths = {"round_dir": round_dir}
            args = SimpleNamespace(step_id="STEP-NEW")
            with mock.patch.object(MOD, "resolve_paths", return_value=paths), \
                 mock.patch.object(MOD, "_restore_round_snapshot_before_tune_retry") as restore:
                MOD._recover_current_round_before_retry_archive(args)

            restore.assert_called_once()
            self.assertEqual(restore.call_args.kwargs["reason"], "same-Round Step retry recovery")


if __name__ == "__main__":
    unittest.main()
