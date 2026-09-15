import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


PATH = Path(__file__).parents[1] / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("optimize_round_retry_archive", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class RoundRetryArchiveTest(unittest.TestCase):
    def test_new_step_archives_entire_existing_round(self):
        with tempfile.TemporaryDirectory() as root:
            output_dir = Path(root) / "optimize/output"
            round_dir = output_dir / "round-001"
            (round_dir / "tune").mkdir(parents=True)
            (round_dir / "tune/diff.patch").write_text("stale diff")
            (round_dir / "round_state.json").write_text(json.dumps({"step_id": "STEP-OLD"}))
            paths = {"round_dir": round_dir, "optimize_output_dir": output_dir}

            with mock.patch.object(MOD, "resolve_paths", return_value=paths):
                archived = MOD._archive_round_for_new_step(Namespace(step_id="STEP-NEW"))

            self.assertIsNotNone(archived)
            self.assertFalse(round_dir.exists())
            self.assertEqual((archived / "tune/diff.patch").read_text(), "stale diff")
            self.assertIn("STEP-OLD", archived.name)

    def test_same_step_keeps_round_for_normal_resume(self):
        with tempfile.TemporaryDirectory() as root:
            output_dir = Path(root) / "optimize/output"
            round_dir = output_dir / "round-001"
            round_dir.mkdir(parents=True)
            (round_dir / "round_state.json").write_text(json.dumps({"step_id": "STEP-SAME"}))
            paths = {"round_dir": round_dir, "optimize_output_dir": output_dir}

            with mock.patch.object(MOD, "resolve_paths", return_value=paths):
                archived = MOD._archive_round_for_new_step(Namespace(step_id="STEP-SAME"))

            self.assertIsNone(archived)
            self.assertTrue(round_dir.exists())


if __name__ == "__main__":
    unittest.main()
