"""Exercise the real executor lifecycle, replacing only the OpenClaw process."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import executor, runtime


class OutputRepairTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "SKILL.md").write_text("# Unchanged business rules")
        (self.root / "input.json").write_text('{"loop":{"round":3}}')
        (self.root / "work").mkdir()
        (self.root / "work/material.md").write_text('# 原文\n包含 "引号"。')
        self.context = {name: str(self.root / filename) for name, filename in (
            ("implementationSkill", "SKILL.md"), ("inputFile", "input.json"),
            ("resultFile", "result.json"),
        )}
        self.calls = []
        self.outputs = []
        self.returncode = 0

    def process(self, command, **kwargs):
        self.calls.append(command)
        if command[1] == "agent":
            output = self.outputs.pop(0)
            if output is not None:
                (self.root / "result.json").write_text(output)
            return subprocess.CompletedProcess(command, self.returncode, "agent finished", "")
        if command[1:3] == ["agents", "delete"]:
            # Cleanup must occur only after the corrected output is acceptable.
            value = json.loads((self.root / "result.json").read_text())
            runtime._normalize_result(executor.collect_content_files(value, self.root))
        return subprocess.CompletedProcess(command, 0, "ok", "")

    def execute(self):
        with patch.object(executor.subprocess, "run", side_effect=self.process):
            return executor.execute_stage_skill(self.context)

    def agent_calls(self):
        return [command for command in self.calls if command[1] == "agent"]

    def test_missing_relative_prefix_is_fed_back_to_same_agent_before_cleanup(self):
        wrong = {"hitl": True, "question": {"format": "form", "title": "确认",
            "contents": [{"id": "material", "title": "材料", "format": "markdown",
                          "contentFile": "material.md"}], "questions": []}}
        corrected = json.loads(json.dumps(wrong))
        corrected["question"]["contents"][0]["contentFile"] = "work/material.md"
        self.outputs = [json.dumps(wrong), json.dumps(corrected)]
        value = self.execute()
        self.assertEqual(value["question"]["contents"][0]["content"], '# 原文\n包含 "引号"。')
        calls = self.agent_calls()
        self.assertEqual(len(calls), 2)
        for argument in ("--agent", "--session-id"):
            self.assertEqual(calls[0][calls[0].index(argument) + 1], calls[1][calls[1].index(argument) + 1])
        feedback = calls[1][calls[1].index("--message") + 1]
        self.assertIn("material.md", feedback)
        self.assertIn("missing", feedback)
        self.assertIn(str(self.root), feedback)
        self.assertEqual(sum(c[1:3] == ["agents", "add"] for c in self.calls), 1)
        self.assertEqual(self.calls[-1][1:3], ["agents", "delete"])
        audit = list(self.root.glob("output-validation/*/attempt-1.result.json"))
        self.assertEqual(len(audit), 1)
        self.assertEqual(json.loads(audit[0].read_text()), wrong)
        self.assertEqual((self.root / "input.json").read_text(), '{"loop":{"round":3}}')
        self.assertEqual((self.root / "SKILL.md").read_text(), "# Unchanged business rules")

    def test_invalid_json_and_envelope_are_corrected_without_platform_rewriting(self):
        for wrong in ('{"summary":"unescaped "quote""}', '{"hitl":"false","result":{}}', '[]'):
            with self.subTest(wrong=wrong):
                self.calls.clear()
                self.outputs = [wrong, '{"summary":"original business text"}']
                self.assertEqual(self.execute(), {"summary": "original business text"})
                self.assertEqual(len(self.agent_calls()), 2)

    def test_missing_result_can_be_created_on_correction(self):
        self.outputs = [None, '{"summary":"created"}']
        self.assertEqual(self.execute(), {"summary": "created"})
        self.assertEqual(len(self.agent_calls()), 2)

    def test_second_invalid_output_fails_and_keeps_agent_evidence(self):
        self.outputs = ['{"summaryFile":"missing.md"}'] * 2
        with self.assertRaisesRegex(executor.CoreDispatchError, "output correction exhausted"):
            self.execute()
        self.assertEqual(len(self.agent_calls()), 2)
        self.assertFalse(any(c[1:3] == ["agents", "delete"] for c in self.calls))

    def test_correction_does_not_weaken_file_boundary(self):
        self.outputs = ['{"summaryFile":"missing.md"}', '{"summaryFile":"../outside.md"}']
        with self.assertRaisesRegex(executor.CoreDispatchError, "escapes"):
            self.execute()
        self.assertEqual(len(self.agent_calls()), 2)

    def test_server_contract_rejection_corrects_form_in_same_agent(self):
        import io
        import urllib.error
        self.context["validationUrl"] = "http://127.0.0.1/api/evolve/internal/tasks/T/steps/S/validate-output"
        wrong = {"question": {"format": "form", "title": "Confirm", "questions": [
            {"id": "q", "type": "text", "title": "Choose"}]}}
        corrected = {"question": {"format": "form", "title": "Confirm", "questions": [
            {"id": "q", "type": "short_text", "title": "Choose"}]}}
        self.outputs = [json.dumps(wrong), json.dumps(corrected)]
        rejection = urllib.error.HTTPError(self.context["validationUrl"], 422, "invalid", {},
            io.BytesIO(b'{"error":"question.questions[0].type unsupported"}'))
        with patch.object(runtime, "_http", side_effect=[rejection, b'{"ok":true}']) as validate:
            self.assertEqual(self.execute(), corrected)
        self.assertEqual(len(self.agent_calls()), 2)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(self.calls[-1][1:3], ["agents", "delete"])
        self.assertIn("type unsupported", self.agent_calls()[1][self.agent_calls()[1].index("--message") + 1])

    def test_validation_service_failure_is_not_model_correction(self):
        import urllib.error
        self.context["validationUrl"] = "http://127.0.0.1/validate-output"
        self.outputs = ['{"summary":"business result"}']
        with patch.object(runtime, "_http", side_effect=urllib.error.HTTPError(
                self.context["validationUrl"], 503, "unavailable", {}, None)):
            with self.assertRaisesRegex(executor.CoreDispatchError, "validation unavailable"):
                self.execute()
        self.assertEqual(len(self.agent_calls()), 1)
        self.assertFalse(any(c[1:3] == ["agents", "delete"] for c in self.calls))

    def test_valid_output_does_not_add_a_model_turn(self):
        self.outputs = ['{"summary":"done"}']
        self.assertEqual(self.execute(), {"summary": "done"})
        self.assertEqual(len(self.agent_calls()), 1)

    def test_process_failure_is_not_retried_as_output_correction(self):
        self.returncode = 1
        self.outputs = [None]
        with self.assertRaisesRegex(executor.CoreDispatchError, "agent failed"):
            self.execute()
        self.assertEqual(len(self.agent_calls()), 1)


if __name__ == "__main__":
    unittest.main()
