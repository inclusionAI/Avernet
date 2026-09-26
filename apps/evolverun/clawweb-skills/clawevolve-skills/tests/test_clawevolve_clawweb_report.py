import importlib.util
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


PATH = Path(__file__).parents[1] / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
SPEC = importlib.util.spec_from_file_location("optimize_report_test", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ClawWebReportResultTest(unittest.TestCase):
    def test_422_json_is_failure(self):
        result = MOD._clawweb_report_result(subprocess.CompletedProcess([], 22, '{"detail":"rejected"}', "curl: (22)"))
        self.assertEqual(result["status"], "UPLOAD_FAILED")

    def test_500_html_is_failure(self):
        result = MOD._clawweb_report_result(subprocess.CompletedProcess([], 22, "<html>error</html>", "curl: (22)"))
        self.assertEqual(result["status"], "UPLOAD_FAILED")

    def test_200_json_ok_is_success(self):
        result = MOD._clawweb_report_result(subprocess.CompletedProcess([], 0, json.dumps({"ok": True}), ""))
        self.assertEqual(result["status"], "SUCCESS")

    def test_200_non_json_is_failure(self):
        result = MOD._clawweb_report_result(subprocess.CompletedProcess([], 0, "ok", ""))
        self.assertEqual(result["status"], "UPLOAD_FAILED")

    def test_baseline_output_requires_all_provenance(self):
        error = MOD._validate_clawweb_baseline_output({
            "train": {
                "role": "train", "source": "reused", "producerStepId": "",
                "ownerUserId": "owner", "domainId": "train", "benchRunId": "bench-train",
                "metrics": {"score": 0.5},
            },
            "test": {
                "role": "test", "source": "reused", "producerStepId": "STEP-1",
                "ownerUserId": "owner", "domainId": "test", "benchRunId": "bench-test",
                "metrics": {"score": 0.4},
            },
        })
        self.assertIn("producerStepId", error)

    def test_pending_fallback_preserves_original_http_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            upload_dir = Path(root) / "upload"
            upload_dir.mkdir()
            original = {"status": "UPLOAD_FAILED", "exitCode": 22, "stdout": "HTTP 422"}
            (upload_dir / "clawweb_manifest.json").write_text(json.dumps(original), encoding="utf-8")
            with mock.patch.object(MOD, "resolve_paths", return_value={"upload_dir": upload_dir}):
                MOD._fallback_pending_clawweb_report(Namespace(round=2), {"message": "semantic failure"})
            self.assertEqual(
                json.loads((upload_dir / "clawweb_manifest.json").read_text()), original
            )
            fallback = json.loads((upload_dir / "clawweb_fallback_manifest.json").read_text())
            self.assertEqual(fallback["status"], "PENDING_RETRY")


if __name__ == "__main__": unittest.main()
