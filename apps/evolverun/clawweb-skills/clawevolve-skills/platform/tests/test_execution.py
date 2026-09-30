import argparse
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform"))
from clawevolve_runtime import executor, runner, runtime

spec = importlib.util.spec_from_file_location("hardening_native", ROOT / "clawevolve-hardening/scripts/run.py")
hardening = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardening)


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.args = argparse.Namespace(
            action="execute", task_id="EV-1", step_id="STEP-1", model="test-model",
            clawweb_url="http://127.0.0.1:5196", message="", goal="test",
            workspace=str(self.root / "candidate"), target=str(self.root / "candidate/target"),
        )
        Path(self.args.target).mkdir(parents=True)
        (Path(self.args.target) / "SKILL.md").write_text("original target")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("SKILL.md", "# Original business rules\nDo the business work.")
        self.package = archive.getvalue()
        self.input = {
            "protocolVersion": "clawevolve.stage-runtime/v1",
            "stage": {"key": "hardening", "mode": "replace"},
            "implementation": {
                "entrypoint": "SKILL.md", "packageSha256": hashlib.sha256(self.package).hexdigest(),
                "package": {"url": "https://artifacts.example/package"},
            },
            "input": {"goal": "business goal", "human_input": {"history": []}},
        }
        self.reports = []
        self.calls = []
        self.result = {"summary": "original conclusion", "changed": False, "changed_files": []}
        self._original_agent = executor._run_openclaw_agent
        for owner, name, value in [
            (runtime, "SOURCE_WORKSPACE", self.root / "source"),
            (runtime, "_http", self.http),
            (executor, "_run_openclaw_agent", self.agent),
            (hardening.hardening_report, "post_report", self.report),
        ]:
            patcher = patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def http(self, method, url, *, body=None, **kwargs):
        if method == "GET" and url.endswith("/input"):
            return json.dumps(self.input).encode()
        if method == "GET" and url == "https://artifacts.example/package":
            return self.package
        if method == "POST" and url.endswith("/report"):
            self.reports.append(json.loads(body))
            return b'{"ok":true}'
        if method == "POST" and url.endswith("/validate-output"):
            runtime._normalize_result(json.loads(body)["output"])
            return b'{"ok":true}'
        raise AssertionError(f"unexpected external call: {method} {url}")

    def agent(self, context, model):
        self.calls.append({"context": context, "model": model})
        self.assertTrue(Path(context["implementationSkill"]).is_file())
        self.assertTrue(Path(context["inputFile"]).is_file())
        Path(context["resultFile"]).write_text(json.dumps(self.result, ensure_ascii=False))
        return {"status": "succeeded"}

    def report(self, **payload):
        self.reports.append(payload)
        return {"ok": True}

    def test_extension_executes_downloaded_business_and_reports_without_wrapper_agent(self):
        runner.execute_extension(self.args)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.reports), 1)
        self.assertEqual(self.reports[0]["output"], {"hitl": False, "result": self.result})
        self.assertEqual(json.loads(Path(self.calls[0]["context"]["inputFile"]).read_text()), self.input["input"])
        prompt = executor._business_prompt(self.calls[0]["context"])
        self.assertNotIn("begin", prompt)
        self.assertNotIn("submit", prompt)
        self.assertNotIn("clawevolve-stage", prompt)

    def test_custom_and_builtin_hardening_share_output_validation_and_reporting(self):
        for custom in (True, False):
            with self.subTest(custom=custom):
                if not custom:
                    self.input = {"protocolVersion": "1.0", "businessInput": {"goal": "builtin input"}}
                self.reports.clear()
                outcome = hardening.run_handler(self.args)
                self.assertEqual(outcome["implementation"], "custom" if custom else "builtin")
                self.assertEqual(len(self.reports), 1)
                self.assertEqual(self.reports[0]["output"]["result"], self.result)

    def test_custom_hardening_cannot_bypass_file_boundary_validation(self):
        self.result = {"summary": "changed", "changed": True, "changed_files": ["../other/SKILL.md"]}
        with self.assertRaisesRegex(ValueError, "escapes target"):
            hardening.run_handler(self.args)
        self.assertEqual(self.reports, [])

    def test_question_and_loop_envelopes_are_preserved_by_native_handler(self):
        form = {"hitl": True, "question": {
            "format": "form", "title": "业务确认", "questions": [],
            "contents": [{"id": "material", "title": "材料", "format": "markdown", "content": "# 原文"}],
        }}
        loop = {"hitl": False, "result": self.result, "loop": {
            "action": "request_feedback", "prompt": "反馈", "accepts": {"text": True, "files": [".txt"]},
        }}
        for value in (form, loop):
            with self.subTest(value=value):
                self.result = value
                self.reports.clear()
                hardening.run_handler(self.args)
                self.assertEqual(self.reports[0]["output"], value)

    def test_native_handler_does_not_send_failure_after_uncertain_success_report(self):
        with patch.object(hardening.hardening_report, "post_report", side_effect=TimeoutError("lost response")) as report:
            with patch.object(hardening, "_parse_args", return_value=self.args):
                self.assertEqual(hardening.main([]), 2)
            self.assertEqual(report.call_count, 1)

    def test_hardening_checks_actual_changes_across_same_step_hitl(self):
        self.result = {"hitl": True, "question": {"format": "form", "title": "Confirm", "questions": [],
            "contents": [{"id": "scope", "title": "Scope", "format": "markdown", "content": "Original scope"}]}}
        hardening.run_handler(self.args)
        (Path(self.args.target) / "SKILL.md").write_text("approved change")
        self.result = {"summary": "changed", "changed": True}
        completed = hardening.run_handler(self.args)
        self.assertEqual(completed["result"]["result"]["changed_files"], ["SKILL.md"])
        # The next feedback Loop starts from the existing candidate, with its
        # own Step snapshot rather than reporting previous changes again.
        self.args.step_id = "STEP-2"
        self.input["input"]["loop"] = {"round": 2, "previous_result": {}, "user_feedback": {"text": "continue", "files": []}}
        self.result = {"summary": "no further change", "changed": False}
        completed = hardening.run_handler(self.args)
        self.assertEqual(completed["result"]["result"]["changed_files"], [])

    def test_hardening_retry_retains_original_round_baseline_after_failed_output(self):
        def model(context, model):
            (Path(self.args.target) / "SKILL.md").write_text("approved change")
            Path(context["resultFile"]).write_text('{"summary":"invalid "quote""}')
            return {"status": "succeeded"}
        with patch.object(executor, "_run_openclaw_agent", model):
            with self.assertRaisesRegex(executor.CoreDispatchError, "not valid JSON"):
                hardening.run_handler(self.args)
        self.assertEqual(self.reports, [])
        self.args.step_id = "STEP-RETRY"
        self.result = {"summary": "approved change already applied", "changed": True}
        completed = hardening.run_handler(self.args)
        self.assertEqual(completed["result"]["result"]["changed_files"], ["SKILL.md"])
        self.args.step_id = "STEP-ROUND-2"
        self.input["input"]["loop"] = {"round": 2, "previous_result": {}, "user_feedback": {"text": "continue", "files": []}}
        self.result = {"summary": "no further change", "changed": False}
        completed = hardening.run_handler(self.args)
        self.assertEqual(completed["result"]["result"]["changed_files"], [])

    def test_unreported_write_outside_target_is_not_accepted(self):
        def model(context, model):
            (Path(self.args.workspace) / "outside.txt").write_text("unexpected write")
            return self.agent(context, model)
        with patch.object(executor, "_run_openclaw_agent", model):
            with self.assertRaisesRegex(ValueError, "outside the target"):
                hardening.run_handler(self.args)
        self.assertEqual(self.reports, [])

    def test_extension_report_loss_does_not_send_a_second_report(self):
        original_http = self.http
        def lose_report(method, url, **kwargs):
            if method == "POST":
                self.reports.append(json.loads(kwargs["body"]))
                raise TimeoutError("lost response")
            return original_http(method, url, **kwargs)
        with patch.object(runtime, "_http", lose_report):
            with self.assertRaises(TimeoutError):
                runner.execute_extension(self.args)
        self.assertEqual(len(self.reports), 1)

    def test_output_correction_reports_once_without_restarting_extension(self):
        commands = []
        def process(command, **kwargs):
            commands.append(command)
            if command[1] == "agent":
                directory = Path(kwargs["cwd"])
                (directory / "work").mkdir(exist_ok=True)
                (directory / "work/summary.md").write_text("Original business conclusion")
                reference = "summary.md" if sum(c[1] == "agent" for c in commands) == 1 else "work/summary.md"
                (directory / "result.json").write_text(json.dumps({"summaryFile": reference}))
            return subprocess.CompletedProcess(command, 0, "ok", "")
        with patch.object(executor, "_run_openclaw_agent", self._original_agent):
            with patch.object(executor.subprocess, "run", side_effect=process):
                runner.execute_extension(self.args)
        self.assertEqual(sum(c[1] == "agent" for c in commands), 2)
        self.assertEqual(len(self.reports), 1)
        self.assertEqual(self.reports[0]["status"], "succeeded")
        self.assertEqual(self.reports[0]["output"], {
            "hitl": False, "result": {"summary": "Original business conclusion"}})

    def test_cli_reports_definite_rejection_but_not_uncertain_http_errors(self):
        for status in (400, 422, 409, 503):
            with self.subTest(status=status):
                self.reports.clear()
                original_http = self.http
                def reject_success(method, url, **kwargs):
                    if method == "POST" and json.loads(kwargs["body"])["status"] == "succeeded":
                        self.reports.append(json.loads(kwargs["body"]))
                        raise urllib.error.HTTPError(url, status, "Rejected", {}, io.BytesIO(b'{"error":"schema violation"}'))
                    return original_http(method, url, **kwargs)
                with patch.object(runtime, "_http", reject_success):
                    code = runner.main(["--action", "execute", "--task-id", "EV-1", "--step-id", "STEP-1",
                                        "--clawweb-url", "http://127.0.0.1:5196"])
                self.assertEqual(code, 1)
                expected = ["succeeded", "failed"] if status in (400, 422) else ["succeeded"]
                self.assertEqual([r["status"] for r in self.reports], expected)
                if status in (400, 422):
                    self.assertIn("schema violation", self.reports[-1]["error"]["message"])

    def test_invalid_business_result_is_failure_not_empty_success(self):
        self.result = {"hitl": "false", "result": {}}
        with self.assertRaises(runtime.RuntimeFailure):
            runner.execute_extension(self.args)
        self.assertEqual(len(self.reports), 1)
        self.assertEqual(self.reports[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
