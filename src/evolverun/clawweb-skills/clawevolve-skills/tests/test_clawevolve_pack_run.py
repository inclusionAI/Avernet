import importlib.util, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest import mock

PATH = Path(__file__).parents[1] / "clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py"
SPEC = importlib.util.spec_from_file_location("pack_run", PATH); MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)

class ErrorReportingTest(unittest.TestCase):
    def test_error_tail_keeps_final_subprocess_failure(self):
        message = "phase-start\n" + ("x" * 2000) + "\nactual restore failure"
        rendered = MOD.error_tail(message, 100)
        self.assertIn("truncated", rendered)
        self.assertTrue(rendered.endswith("actual restore failure"))

class RestoreIntegrityTest(unittest.TestCase):
    def test_hash_mismatch_never_invokes_deploy(self):
        with tempfile.TemporaryDirectory() as root:
            artifact = {"ref": "oss://clawevolve-artifacts/evolution/S/snapshots/artifact.zip", "size": 1, "sha256": "a" * 64, "contentType": "application/zip", "kind": "pack"}
            out = Path(root); manifest = {"schemaVersion":"clawevolve.snapshot.v1", "taskId":"S", "artifact": artifact}
            class Client:
                calls = 0
                def download_restore(self, kind, path):
                    self.calls += 1
                    if self.calls == 1: Path(path).write_text(__import__('json').dumps(manifest))
                    else:
                        with zipfile.ZipFile(path, "w") as z: z.writestr("x", "y")
                    return {"artifact": artifact}
            args = type("Args", (), {"source_kind":"snapshot", "source_task_id":"S", "source_round":0, "task_id":"R", "step_id":"P"})()
            with mock.patch.object(MOD, "frozen_restore_input", return_value=artifact), mock.patch.object(MOD.subprocess, "run") as deploy:
                with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                    MOD.run_restore(args, Client(), out)
                deploy.assert_not_called()

class ReportRetryTest(unittest.TestCase):
    def test_success_report_failure_is_not_reported_as_business_failure(self):
        with tempfile.TemporaryDirectory() as root:
            output = {"summary": "Pack 已创建", "pack": {"status": "available"}}
            argv = ["clawevolve_pack_run.py", "--task-id", "EV-T", "--step-id", "STEP-S", "--mode", "pack"]
            with mock.patch.object(MOD, "WORKSPACE", Path(root)), \
                 mock.patch.object(MOD, "artifact_client", return_value=object()), \
                 mock.patch.object(MOD, "run_pack", return_value=(output, Path(root) / "state.json")) as run_pack, \
                 mock.patch.object(MOD, "report", side_effect=RuntimeError("network lost")) as report, \
                 mock.patch.object(sys, "argv", argv):
                MOD.write_json(Path(root) / "state.json", {"clawwebReported": False})
                with self.assertRaisesRegex(RuntimeError, "network lost"):
                    MOD.main()
            run_pack.assert_called_once()
            report.assert_called_once()
            self.assertEqual(report.call_args.args[1], "succeeded")
            self.assertTrue((Path(root) / "clawevolve_results/EV-T/pack/result.json").exists())

    def test_retry_reuses_business_result_and_only_reports_success(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "clawevolve_results/EV-T/pack"
            output = {"summary": "Pack 已创建", "pack": {"status": "available"}}
            MOD.write_json(out / "result.json", output)
            MOD.write_json(out / "snapshot-state.json", {"clawwebReported": False})
            argv = ["clawevolve_pack_run.py", "--task-id", "EV-T", "--step-id", "STEP-S", "--mode", "pack"]
            with mock.patch.object(MOD, "WORKSPACE", Path(root)), \
                 mock.patch.object(MOD, "run_pack") as run_pack, \
                 mock.patch.object(MOD, "report") as report, \
                 mock.patch.object(sys, "argv", argv):
                MOD.main()
            run_pack.assert_not_called()
            report.assert_called_once()
            self.assertTrue(MOD.load_json(out / "snapshot-state.json")["clawwebReported"])

    def test_manifest_must_equal_frozen_artifact_before_deploy(self):
        with tempfile.TemporaryDirectory() as root:
            frozen = {"ref":"oss://clawevolve-artifacts/evolution/S/snapshots/artifact.zip", "size":1, "sha256":"a"*64, "contentType":"application/zip", "kind":"pack"}
            changed = {**frozen, "sha256":"b"*64}
            manifest = {"schemaVersion":"clawevolve.snapshot.v1", "taskId":"S", "artifact":changed}
            class Client:
                def download_restore(self, kind, path): Path(path).write_text(__import__('json').dumps(manifest)); return {"artifact": frozen}
            args = type("Args", (), {"source_kind":"snapshot", "source_task_id":"S", "source_round":0, "task_id":"R", "step_id":"P"})()
            with mock.patch.object(MOD, "frozen_restore_input", return_value=frozen), mock.patch.object(MOD.subprocess, "run") as deploy:
                with self.assertRaisesRegex(RuntimeError, "frozen Step Input"):
                    MOD.run_restore(args, Client(), Path(root))
                deploy.assert_not_called()

    def test_manifest_task_mismatch_before_deploy(self):
        with tempfile.TemporaryDirectory() as root:
            artifact = {"ref":"oss://clawevolve-artifacts/evolution/S/snapshots/artifact.zip", "size":1, "sha256":"a"*64, "contentType":"application/zip", "kind":"pack"}
            manifest = {"schemaVersion":"clawevolve.snapshot.v1", "taskId":"OTHER", "artifact":artifact}
            class Client:
                def download_restore(self, kind, path): Path(path).write_text(__import__('json').dumps(manifest)); return {"artifact": artifact}
            args = type("Args", (), {"source_kind":"snapshot", "source_task_id":"S", "source_round":0, "task_id":"R", "step_id":"P"})()
            with mock.patch.object(MOD, "frozen_restore_input", return_value=artifact), mock.patch.object(MOD.subprocess, "run") as deploy:
                with self.assertRaisesRegex(RuntimeError, "task mismatch"):
                    MOD.run_restore(args, Client(), Path(root))
                deploy.assert_not_called()


class LocalNamespaceTest(unittest.TestCase):
    def test_local_namespace_and_source_checks(self):
        for kind, suffix, round_no in [("snapshot", "snapshots/artifact.zip", 0), ("baseline", "baseline/artifact_v0.zip", 0), ("round", "rounds/round-002/artifacts/artifact_v2.zip", 2)]:
            args = type("Args", (), {"source_kind": kind, "source_task_id": "S", "source_round": round_no, "artifact_bucket": "clawevolve-artifacts"})()
            value = {"ref": f"oss://clawevolve-artifacts/evolution/S/{suffix}", "size": 1, "sha256": "a"*64, "contentType": "application/zip"}
            MOD.validate_artifact(value, args)
            for wrong in [value["ref"].replace("/S/", "/OTHER/"), value["ref"].replace("clawevolve-artifacts", "other-bucket")]:
                with self.assertRaises(RuntimeError): MOD.validate_artifact({**value, "ref": wrong}, args)

    def test_internal_cli_rejects_namespace_override(self):
        argv = ["pack", "--mode", "restore", "--task-id", "T", "--step-id", "S", "--artifact-bucket", "clawevolve-artifacts"]
        with mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit): MOD.main()

if __name__ == "__main__": unittest.main()
