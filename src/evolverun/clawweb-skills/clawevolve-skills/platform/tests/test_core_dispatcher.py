import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import executor as dispatcher


class CoreDispatcherTest(unittest.TestCase):
    def test_builtin_path_calls_only_the_native_core(self):
        native = Mock(return_value="native-result")
        custom = Mock()

        result = dispatcher.dispatch_stage_core(
            selection={"selected": False},
            run_builtin=native,
            run_custom=custom,
        )

        self.assertEqual(result, dispatcher.CoreDispatchResult(selected=False, value="native-result"))
        native.assert_called_once_with()
        custom.assert_not_called()

    def test_replace_path_calls_only_the_custom_core(self):
        native = Mock()
        custom = Mock(return_value="custom-result")
        selection = {
            "selected": True,
            "implementationSkill": "/runtime/package/SKILL.md",
            "inputFile": "/runtime/input.json",
            "resultFile": "/runtime/result.json",
        }

        result = dispatcher.dispatch_stage_core(
            selection=selection,
            run_builtin=native,
            run_custom=custom,
        )

        self.assertEqual(result, dispatcher.CoreDispatchResult(selected=True, value="custom-result"))
        native.assert_not_called()
        custom.assert_called_once_with(selection)

    def test_custom_executor_requires_the_business_result_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "package" / "SKILL.md"
            input_file = root / "input.json"
            result_file = root / "result.json"
            skill.parent.mkdir()
            skill.write_text("# business", encoding="utf-8")
            input_file.write_text("{}", encoding="utf-8")
            runner = Mock(return_value={
                "status": "succeeded",
                "agent_id": "stage-agent-1",
                "session_id": "stage-session-1",
                "stdout": '{"status":"ok","result":{"payloads":[{"text":"只输出了说明"}]}}',
            })

            with self.assertRaisesRegex(
                dispatcher.CoreDispatchError,
                "agent=stage-agent-1; session=stage-session-1; final_output=.*只输出了说明",
            ):
                dispatcher.execute_stage_skill(
                    {
                        "implementationSkill": str(skill),
                        "inputFile": str(input_file),
                        "resultFile": str(result_file),
                    },
                    model="",
                    agent_runner=runner,
                )


if __name__ == "__main__":
    unittest.main()
