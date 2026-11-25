"""Retry only delivery of a frozen report, never business execution."""
import io
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import runtime


class ReportRetryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.args = SimpleNamespace(task_id="EV-1", step_id="STEP-1", clawweb_url="http://localhost:1")
        for target, value in (("SOURCE_WORKSPACE", self.root),):
            p = patch.object(runtime, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(runtime.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def http_error(self, status):
        return urllib.error.HTTPError("http://localhost:1/report", status, "failure", {}, io.BytesIO(b"failure"))

    def test_transient_failures_replay_identical_body_and_identity(self):
        with patch.object(runtime, "_http", side_effect=[self.http_error(502), TimeoutError("lost"), b'{"ok":true}']) as send:
            result = runtime._report(self.args, "succeeded", "done", output={"prepared": True})
        self.assertTrue(result["ok"])
        self.assertEqual(send.call_count, 3)
        self.assertEqual(send.call_args_list[0], send.call_args_list[1])
        self.assertEqual(send.call_args_list[1], send.call_args_list[2])
        self.assertIn("X-ClawEvolve-Report-Id", send.call_args.kwargs["headers"])
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1, 2])

    def test_persistent_failure_is_bounded_and_preserves_payload(self):
        with patch.object(runtime, "_http", side_effect=lambda *a, **k: self.raise_502()) as send:
            with self.assertRaises(runtime.RuntimeFailure):
                runtime._report(self.args, "succeeded", "done", output={"prepared": True})
        self.assertEqual(send.call_count, 5)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1, 2, 4, 8])
        evidence = list(runtime._run_dir(self.args).glob("report-*.json"))
        self.assertEqual(len(evidence), 1)
        value = json.loads(evidence[0].read_text())
        self.assertEqual(value["payload"]["output"], {"prepared": True})
        self.assertEqual(value["delivery"], "unconfirmed")
        self.assertEqual(len(value["attempts"]), 5)
        self.assertTrue(self.args._report_attempted)
        self.assertEqual(evidence[0].stat().st_mode & 0o777, 0o600)

    def raise_502(self):
        raise self.http_error(502)

    def test_definite_rejections_are_not_retried(self):
        for status in (400, 401, 403, 404, 409, 422):
            with self.subTest(status=status), patch.object(runtime, "_http", side_effect=self.http_error(status)) as send:
                with self.assertRaises(runtime.RuntimeFailure):
                    runtime._report(self.args, "succeeded", "done")
                self.assertEqual(send.call_count, 1)
                self.assertEqual(self.args._report_attempted, status not in (400, 422))

    def test_unacknowledged_response_is_not_success(self):
        with patch.object(runtime, "_http", return_value=b'{"ok":false}') as send:
            with self.assertRaises(runtime.RuntimeFailure):
                runtime._report(self.args, "succeeded", "done")
        self.assertEqual(send.call_count, 1)
        self.assertTrue(self.args._report_attempted)

    def test_elapsed_budget_stops_retrying(self):
        with patch.object(runtime, "_http", side_effect=self.http_error(504)) as send, \
                patch.object(runtime.time, "monotonic", side_effect=[0, 0, 119]):
            with self.assertRaises(runtime.RuntimeFailure):
                runtime._report(self.args, "succeeded", "done")
        self.assertEqual(send.call_count, 1)
        self.sleep.assert_not_called()

    def test_hardening_uses_the_same_report_delivery(self):
        path = Path(__file__).resolve().parents[2] / "clawevolve-hardening/scripts/report.py"
        spec = importlib.util.spec_from_file_location("hardening_report_retry", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(runtime, "_http", side_effect=[self.http_error(503), b'{"ok":true}']) as send:
            result = module.post_report(status="succeeded", task_id="EV-1", step_id="STEP-1",
                                        clawweb_url="http://localhost:1", summary="done", output={"changed": False})
        self.assertTrue(result["ok"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0], send.call_args_list[1])

    def test_gateway_error_body_disconnect_still_retries_the_report(self):
        error = self.http_error(502)
        with patch.object(error, "read", side_effect=ConnectionResetError("body disconnected")), \
                patch.object(runtime, "_http", side_effect=[error, b'{"ok":true}']) as send:
            self.assertTrue(runtime._report(self.args, "succeeded", "done")["ok"])
        self.assertEqual(send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
