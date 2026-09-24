import argparse
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import runtime


def args():
    return argparse.Namespace(
        task_id="EV-CORE-1",
        step_id="STEP-CORE-1",
        clawweb_url="http://127.0.0.1:5195",
        action="execute",
        message="",
    )


class CoreSelectionTest(unittest.TestCase):
    def test_normal_native_step_does_not_enter_custom_core(self):
        with patch.object(runtime, "_step_input", return_value={"protocolVersion": "1.0"}), \
                patch.object(runtime, "_begin", side_effect=AssertionError("must not begin")):
            self.assertEqual(runtime._begin_core(args()), {"selected": False})

    def test_selected_core_reuses_the_exact_fetched_payload(self):
        payload = {
            "protocolVersion": "clawevolve.stage-runtime/v1",
            "implementation": {"implementationId": "IMPL-1"},
        }
        context = {
            "implementationSkill": "/runtime/package/SKILL.md",
            "inputFile": "/runtime/input.json",
            "resultFile": "/runtime/result.json",
        }
        with patch.object(runtime, "_step_input", return_value=payload), \
                patch.object(runtime, "_begin", return_value=context) as begin:
            self.assertEqual(runtime._begin_core(args()), {"selected": True, **context})
            begin.assert_called_once_with(args(), payload)

    def test_completed_result_can_request_another_feedback_round(self):
        value = {
            "hitl": False,
            "result": {"summary": "本轮完成"},
            "loop": {
                "action": "request_feedback",
                "prompt": "接受结果，或继续提出意见。",
                "accepts": {"text": True, "files": [".txt", ".xlsx"]},
            },
        }
        self.assertEqual(runtime._normalize_result(value), value)

    def test_structured_question_can_contain_read_only_markdown_pages(self):
        value = {
            "hitl": True,
            "question": {
                "format": "form",
                "title": "请确认升级问题清单",
                "contents": [{
                    "id": "question-list",
                    "title": "升级问题清单",
                    "format": "markdown",
                    "content": "# 原始只读材料",
                }],
                "questions": [],
            },
        }
        self.assertEqual(runtime._normalize_result(value), value)

    def test_structured_question_requires_content_or_question(self):
        value = {
            "hitl": True,
            "question": {"format": "form", "title": "空表单", "contents": [], "questions": []},
        }
        with self.assertRaisesRegex(runtime.RuntimeFailure, "title/contents/questions are invalid"):
            runtime._normalize_result(value)

    def test_loop_feedback_files_are_downloaded_and_replaced_with_local_paths(self):
        content = b"abc"
        value = {"loop": {"round": 2, "previous_result": {"summary": "v1"}, "user_feedback": {
            "text": "再检查一轮",
            "files": [{
                "name": "rules.txt", "content_type": "text/plain", "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "ref": "oss://bucket/key", "download": {"method": "GET", "url": "https://signed"},
            }],
        }}}
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "_http", return_value=content):
            result = runtime._materialize_loop_feedback(value, Path(directory))
            file = result["loop"]["user_feedback"]["files"][0]
            self.assertEqual(Path(file["path"]).read_bytes(), content)
            self.assertNotIn("download", file)
            self.assertNotIn("ref", file)
