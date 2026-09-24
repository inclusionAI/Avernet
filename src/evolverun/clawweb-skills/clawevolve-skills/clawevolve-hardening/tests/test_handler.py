import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "hardening_handler", Path(__file__).resolve().parents[1] / "scripts/run.py"
)
handler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = handler
SPEC.loader.exec_module(handler)


def args():
    return argparse.Namespace(
        task_id="EV-1", step_id="STEP-1", workspace="/candidate/workspace",
        target="/candidate/workspace/skills/target", goal="加固", model="GLM-5.2",
        clawweb_url="http://127.0.0.1:5196",
    )


class HardeningHandlerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        patcher = patch.object(handler.runtime, "SOURCE_WORKSPACE", self.root / "runtime")
        patcher.start()
        self.addCleanup(patcher.stop)

    def invocation(self):
        value = args()
        value.workspace = str(self.root / "workspace")
        value.target = str(self.root / "workspace/skills/target")
        Path(value.target).mkdir(parents=True, exist_ok=True)
        return value

    def test_parser_preserves_an_unquoted_multi_word_goal_from_the_launcher(self):
        parsed = handler._parse_args([
            "/clawevolve-hardening --task-id EV-1 --step-id STEP-1 "
            "--workspace /candidate/workspace --target /candidate/workspace/skills/target "
            "--goal 验证 97 Skill 加固实现完整保留原业务逻辑 "
            "--clawweb-url http://127.0.0.1:5196"
        ])

        self.assertEqual(parsed.goal, "验证 97 Skill 加固实现完整保留原业务逻辑")

    def test_local_logical_paths_audit_the_same_candidate_as_the_business_core(self):
        invocation = args()
        invocation.workspace = "/home/admin/.openclaw/clawevolve_workspaces/EV-1"
        invocation.target = invocation.workspace + "/skills/target"
        actual_target = self.root / ".openclaw/clawevolve_workspaces/EV-1/skills/target"
        actual_target.mkdir(parents=True)
        skill = actual_target / "SKILL.md"
        skill.write_text("before")

        def execute(context, **kwargs):
            skill.write_text("after")
            return {"summary": "完成", "changed": True, "changed_files": ["SKILL.md"]}

        with patch.object(handler.runtime, "RUNTIME_LAYOUT_HOME", self.root), \
                patch.object(handler.core_dispatcher, "begin_stage_core", return_value={"selected": False}), \
                patch.object(handler.core_dispatcher, "execute_stage_skill", side_effect=execute), \
                patch.object(handler.hardening_report, "post_report", return_value={"ok": True}):
            result = handler.run_handler(invocation)

        self.assertEqual(result["result"]["result"]["changed_files"], ["SKILL.md"])

    def test_replace_runs_custom_core_and_submits_without_builtin(self):
        input_file = self.root / "custom-core-input.json"
        input_file.write_text('{"loop":{"round":1}}')
        context = {
            "selected": True,
            "implementationSkill": "/runtime/SKILL.md",
            "inputFile": str(input_file),
            "resultFile": "/runtime/result.json",
        }
        with patch.object(handler.core_dispatcher, "begin_stage_core", return_value=context), \
                patch.object(handler.core_dispatcher, "execute_stage_skill", return_value={"summary": "ok", "changed": False}) as execute, \
                patch.object(handler.hardening_report, "post_report", return_value={"ok": True}) as submit, \
                patch.object(handler, "_builtin_context", side_effect=AssertionError("builtin must not run")):
            result = handler.run_handler(self.invocation())

        self.assertEqual(result["implementation"], "custom")
        execute.assert_called_once_with(context, model="GLM-5.2")
        submit.assert_called_once()

    def test_builtin_runs_default_core_then_reports_through_handler(self):
        business = {"summary": "完成", "changed": False, "changed_files": []}
        context = {"implementationSkill": "/builtin/SKILL.md", "inputFile": "/in", "resultFile": "/out"}
        with patch.object(handler.core_dispatcher, "begin_stage_core", return_value={"selected": False}), \
                patch.object(handler, "_builtin_context", return_value=context), \
                patch.object(handler.core_dispatcher, "execute_stage_skill", return_value=business), \
                patch.object(handler.hardening_report, "post_report", return_value={"ok": True}) as report:
            result = handler.run_handler(self.invocation())

        self.assertEqual(result["implementation"], "builtin")
        report.assert_called_once()

    def test_core_protocol_failure_is_reported_as_retryable_infrastructure_failure(self):
        failure = handler.core_dispatcher.CoreDispatchError("result.json missing")
        with patch.object(handler, "_parse_args", return_value=args()), \
                patch.object(handler, "run_handler", side_effect=failure), \
                patch.object(handler.hardening_report, "post_report", return_value={"ok": True}) as report:
            exit_code = handler.main([])

        self.assertEqual(exit_code, 2)
        report.assert_called_once_with(
            status="failed", task_id="EV-1", step_id="STEP-1",
            clawweb_url="http://127.0.0.1:5196",
            summary="CoreDispatchError: result.json missing", output=None,
            error_code="STAGE_CORE_EXECUTION_FAILED", retryable=True,
        )


if __name__ == "__main__":
    unittest.main()
