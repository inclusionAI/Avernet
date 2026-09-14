import argparse
import importlib.util
import tempfile
import unittest
import urllib.error
import json
import os
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/handlers/clawevolve_bench_run.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_bench_run", SCRIPT)
assert SPEC and SPEC.loader
handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handler)


class BenchHandlerConfigTests(unittest.TestCase):
    def test_diagnostic_is_written_to_file_without_pretty_printing(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "logs/handler.log"
            with mock.patch.object(handler.sys, "stderr"):
                handler.diagnostic(log_path, "test.event", taskId="EV-1", domainId="blog")
            record = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(record["event"], "test.event")
            self.assertEqual(record["taskId"], "EV-1")

    def test_http_error_includes_clawweb_validation_message(self):
        error = urllib.error.HTTPError("https://clawweb.test/report", 422, "Unprocessable Entity", {}, None)
        error.read = mock.Mock(return_value=b'{"error":"Bench Run reference mismatch"}')
        with mock.patch.object(handler.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Bench Run reference mismatch"):
                handler.http_json("POST", "https://clawweb.test/report", {"status": "succeeded"})

    def test_cli_accepts_only_kebab_case(self):
        parser = handler.build_parser()
        args = parser.parse_args(["--task-id", "EV-1", "--step-id", "STEP-1", "--domain-id", "blog"])
        self.assertEqual(args.domain_id, "blog")
        with self.assertRaises(SystemExit):
            parser.parse_args(["--task-id", "EV-1", "--step-id", "STEP-1", "--domainId", "blog"])

    def payload(self) -> dict:
        return {
            "task": {"taskId": "EV-1", "userId": "u1"},
            "step": {"stepId": "STEP-1"},
            "input": {
                "bench": {
                    "domainId": "blog",
                    "templateName": "write-blog",
                    "templateVersion": None,
                    "model": "antchat/GLM-5",
                    "suite": "all",
                    "scene": "claw-evolve-bench",
                    "judge": {"model": "judge-v1", "apiKeyRef": "env:JUDGE_KEY"},
                },
                "ownerUserId": "u1",
                "pinnedTemplates": [{"templateName": "write-blog", "templateVersion": 3}],
            },
        }

    def args(self, **overrides) -> argparse.Namespace:
        values = {
            "task_id": "EV-1", "step_id": "STEP-1", "domain_id": "blog",
            "template_name": "write-blog", "template_version": "3", "owner_id": "u1",
            "model": "antchat/GLM-5", "suite": "all", "scene": "claw-evolve-bench",
            "judge": "judge-v1",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_legacy_workflow_parameters_are_verified_and_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            private_root = root / "clawevolve-skills"
            with mock.patch.dict(os.environ, {"SKILL_BASE_DIR": str(private_root)}):
                config = handler.workflow_config(self.payload(), self.args(), root, root / "bench/STEP-1", "https://clawweb.test")
        self.assertEqual(config["bench"]["domainId"], "blog")
        self.assertEqual(config["bench"]["pinnedTemplates"][0]["templateVersion"], 3)
        self.assertEqual(config["bench"]["judgeApiKeyRef"], "env:JUDGE_KEY")
        self.assertNotIn("judgeApiKey", config["bench"])
        self.assertEqual(config["runtime"]["inputDir"], str(root / "bench/STEP-1/input"))
        self.assertEqual(config["runtime"]["outputDir"], str(root / "bench/STEP-1/output"))
        self.assertEqual(config["runtime"]["agentbenchHome"], str(private_root.resolve() / "clawbench-base"))

    def test_command_parameter_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "model mismatch"):
                root = Path(temp)
                handler.workflow_config(
                    self.payload(), self.args(model="different-model"), root, root / "bench/STEP-1", "https://clawweb.test",
                )


if __name__ == "__main__":
    unittest.main()
