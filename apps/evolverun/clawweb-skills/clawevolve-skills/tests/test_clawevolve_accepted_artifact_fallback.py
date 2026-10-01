import importlib.util
import json
import tempfile
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest import mock


PATH = Path(__file__).parents[1] / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("optimize_accepted_fallback", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class AcceptedArtifactFallbackTest(unittest.TestCase):
    def test_legacy_string_manifest_is_not_guessed(self):
        self.assertEqual(MOD._accepted_artifact_record({"last_accepted_artifact": "/tmp/a.zip"}), {})

    def test_missing_local_pack_is_downloaded_and_zip_checked(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "artifact_v1.zip"
            artifact = {
                "ref": "oss://clawevolve-artifacts/evolution/EV-1/rounds/round-001/artifacts/artifact_v1.zip",
                "size": 0, "sha256": "", "contentType": "application/zip",
            }

            class Client:
                def download_accepted(self, source_round, path):
                    self.source_round = source_round
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("manifest.json", "{}")
                    artifact["size"] = path.stat().st_size
                    artifact["sha256"] = MOD._sha256(path)
                    return {"artifact": dict(artifact)}

            with mock.patch.object(MOD, "_artifact_client", return_value=Client()):
                result = MOD._ensure_accepted_artifact_local(object(), 1, target)
            self.assertEqual(result, target)
            self.assertTrue(zipfile.is_zipfile(target))

    def test_round_one_prepare_freezes_plan_objective_json(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            run_dir = workspace / "clawevolve_results/EV-O"
            plan_output = run_dir / "plan/output"
            plan_output.mkdir(parents=True)
            (plan_output / "objective.md").write_text("objective", encoding="utf-8")
            objective = {
                "primary_metric": {
                    "name": "task_success_rate", "display_name": "任务成功率",
                    "operator": ">=", "target": 0.9, "unit": "ratio",
                },
            }
            (plan_output / "objective.json").write_text(json.dumps(objective), encoding="utf-8")
            (plan_output / "spec-v0.md").write_text("spec", encoding="utf-8")
            (plan_output / "spec-v0.json").write_text(json.dumps({}), encoding="utf-8")
            skill_base = workspace / "skills"
            skill_base.mkdir(parents=True)
            args = Namespace(task_id="EV-O", round=1, step_id="STEP-1", workspace=str(workspace),
                             skill_base_dir=str(skill_base))

            with mock.patch.object(MOD, "_record_round_identity", return_value={}), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), \
                 mock.patch.object(MOD, "_print_json"):
                MOD.action_prepare(args)

            frozen = json.loads((run_dir / "optimize/input/objective.json").read_text())
            self.assertEqual(frozen, objective)
            state = json.loads((run_dir / "optimize/output/round-001/round_state.json").read_text())
            self.assertEqual(state["objective_json"], str(run_dir / "optimize/input/objective.json"))

    def test_round_two_prepare_uses_task_start_pack_after_round_one_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            run_dir = workspace / "clawevolve_results/EV-T"
            optimize_input = run_dir / "optimize/input"
            optimize_output = run_dir / "optimize/output"
            baseline = optimize_input / "baseline"
            baseline.mkdir(parents=True)
            (optimize_input / "objective.md").write_text("objective")
            (optimize_input / "objective.json").write_text(json.dumps({
                "primary_metric": {
                    "name": "task_success_rate", "display_name": "任务成功率",
                    "operator": ">=", "target": 0.9, "unit": "ratio",
                },
            }))
            round_one = optimize_output / "round-001"
            (round_one / "spec").mkdir(parents=True)
            (round_one / "spec/spec-v1.md").write_text("spec")
            (round_one / "acceptance").mkdir()
            (round_one / "acceptance/acceptance_report.json").write_text(json.dumps({"accepted": False}))
            (round_one / "round_state.json").write_text(json.dumps({"accepted": False}))

            artifact = baseline / "artifact_v0.zip"
            artifact.write_bytes(b"task-start-pack")
            published = {
                "ref": "oss://clawevolve-artifacts/evolution/EV-T/baseline/artifact_v0.zip",
                "size": artifact.stat().st_size,
                "sha256": MOD._sha256(artifact),
                "contentType": "application/zip",
            }
            (baseline / "baseline-manifest.json").write_text(json.dumps({
                "publishStatus": "SUCCESS", "artifactUploaded": True, "manifestUploaded": True,
                "sha256": MOD._sha256(artifact), "publishedArtifact": published,
            }))
            skill_base = workspace / "skills"
            skill_base.mkdir(parents=True)
            args = Namespace(task_id="EV-T", round=2, step_id="STEP-2", workspace=str(workspace),
                             skill_base_dir=str(skill_base))

            with mock.patch.object(MOD, "_record_round_identity", return_value={}), \
                 mock.patch.object(MOD, "_mark_step"), mock.patch.object(MOD, "_log"), \
                 mock.patch.object(MOD, "_print_json"):
                MOD.action_prepare(args)

            state = json.loads((optimize_output / "round-002/round_state.json").read_text())
            self.assertEqual(state["baseline_artifact"], str(artifact))
            self.assertEqual(state["baselineArtifact"]["artifact"], published)

    def test_failed_prepare_state_is_not_treated_as_completed(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            round_dir = root_path / "round-002"
            input_dir = round_dir / "input"
            input_dir.mkdir(parents=True)
            (input_dir / "spec-v1.md").write_text("spec")
            (round_dir / "round_state.json").write_text(json.dumps({
                "status": "ROUND_FAILED",
                "steps": {"prepare": {"status": "RUNNING"}},
            }))
            paths = {"round_dir": round_dir, "input_dir": input_dir}
            args = Namespace(round=2)
            with mock.patch.object(MOD, "resolve_paths", return_value=paths):
                self.assertFalse(MOD._step_done(args, "prepare"))


if __name__ == "__main__": unittest.main()
