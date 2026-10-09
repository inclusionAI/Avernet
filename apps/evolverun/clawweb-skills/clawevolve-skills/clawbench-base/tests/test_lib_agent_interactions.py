from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import lib_agent  # noqa: E402
from lib_tasks import Task  # noqa: E402


def _task(frontmatter: dict) -> Task:
    return Task(
        task_id="task_interactive",
        name="Interactive",
        category="test",
        grading_type="automated",
        timeout_seconds=120,
        workspace_files=[],
        prompt="start",
        expected_behavior="",
        grading_criteria=[],
        frontmatter=frontmatter,
    )


def _msg(role: str, text: str) -> dict:
    return {"type": "message", "message": {"role": role, "content": [{"type": "text", "text": text}]}}


def _completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["openclaw"], returncode=0, stdout=stdout, stderr="")


class InteractionExecutionTests(unittest.TestCase):
    def test_rules_can_trigger_multiple_turns_from_new_assistant_output(self) -> None:
        task = _task(
            {
                "interactions": {
                    "max_turns": 3,
                    "rules": [{"on_output_contains": "等待用户输入", "reply": "ok"}],
                }
            }
        )
        transcripts = [
            ([_msg("user", "start"), _msg("assistant", "等待用户输入")], None),
            (
                [
                    _msg("user", "start"),
                    _msg("assistant", "等待用户输入"),
                    _msg("user", "ok"),
                    _msg("assistant", "再次等待用户输入"),
                ],
                None,
            ),
            (
                [
                    _msg("user", "start"),
                    _msg("assistant", "等待用户输入"),
                    _msg("user", "ok"),
                    _msg("assistant", "再次等待用户输入"),
                    _msg("user", "ok"),
                    _msg("assistant", "done"),
                ],
                None,
            ),
            (
                [
                    _msg("user", "start"),
                    _msg("assistant", "等待用户输入"),
                    _msg("user", "ok"),
                    _msg("assistant", "再次等待用户输入"),
                    _msg("user", "ok"),
                    _msg("assistant", "done"),
                ],
                None,
            ),
        ]

        with tempfile.TemporaryDirectory() as temp_dir, \
            patch.object(lib_agent, "cleanup_agent_sessions"), \
            patch.object(lib_agent, "prepare_task_workspace", return_value=Path(temp_dir)), \
            patch.object(lib_agent, "_run_openclaw_message", side_effect=[_completed(), _completed(), _completed()]) as run_mock, \
            patch.object(lib_agent, "_load_transcript", side_effect=transcripts):
            result = lib_agent.execute_openclaw_task(
                task=task,
                agent_id="bench-agent",
                model_id="model",
                run_id="run",
                timeout_multiplier=1.0,
                skill_dir=Path(temp_dir),
            )

        sent_messages = [call.kwargs["message"] for call in run_mock.call_args_list]
        self.assertEqual(sent_messages, ["start", "ok", "ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["interaction_events"]), 3)
        self.assertEqual(result["interaction_events"][0]["source"], "rule")
        self.assertEqual(result["interaction_events"][1]["source"], "rule")
        self.assertEqual(result["interaction_events"][2]["source"], "none")

    def test_rules_take_priority_over_judge_policy(self) -> None:
        task = _task(
            {
                "interactions": {
                    "max_turns": 2,
                    "rules": [{"on_output_contains": "等待用户输入", "reply": "ok"}],
                    "judge_policy": {"instruction": "判断是否回复"},
                }
            }
        )
        transcripts = [
            ([_msg("user", "start"), _msg("assistant", "等待用户输入")], None),
            ([_msg("user", "start"), _msg("assistant", "等待用户输入"), _msg("user", "ok"), _msg("assistant", "done")], None),
            ([_msg("user", "start"), _msg("assistant", "等待用户输入"), _msg("user", "ok"), _msg("assistant", "done")], None),
        ]

        with tempfile.TemporaryDirectory() as temp_dir, \
            patch.object(lib_agent, "cleanup_agent_sessions"), \
            patch.object(lib_agent, "prepare_task_workspace", return_value=Path(temp_dir)), \
            patch.object(lib_agent, "_ensure_interaction_judge_agent", return_value="bench-judge"), \
            patch.object(lib_agent, "_call_interaction_judge") as judge_mock, \
            patch.object(lib_agent, "_run_openclaw_message", side_effect=[_completed(), _completed()]), \
            patch.object(lib_agent, "_load_transcript", side_effect=transcripts):
            result = lib_agent.execute_openclaw_task(
                task=task,
                agent_id="bench-agent",
                model_id="model",
                run_id="run",
                timeout_multiplier=1.0,
                skill_dir=Path(temp_dir),
            )

        judge_mock.assert_called_once()
        self.assertEqual(result["interaction_events"][0]["source"], "rule")

    def test_judge_policy_can_reply_when_rules_do_not_match(self) -> None:
        task = _task(
            {
                "interactions": {
                    "max_turns": 2,
                    "rules": [{"on_output_contains": "等待用户输入", "reply": "ok"}],
                    "judge_policy": {"instruction": "如果 assistant 请求确认，回复 ok"},
                }
            }
        )
        transcripts = [
            ([_msg("user", "start"), _msg("assistant", "请确认是否继续")], None),
            ([_msg("user", "start"), _msg("assistant", "请确认是否继续"), _msg("user", "ok"), _msg("assistant", "done")], None),
            ([_msg("user", "start"), _msg("assistant", "请确认是否继续"), _msg("user", "ok"), _msg("assistant", "done")], None),
        ]

        with tempfile.TemporaryDirectory() as temp_dir, \
            patch.object(lib_agent, "cleanup_agent_sessions"), \
            patch.object(lib_agent, "prepare_task_workspace", return_value=Path(temp_dir)), \
            patch.object(lib_agent, "_ensure_interaction_judge_agent", return_value="bench-judge"), \
            patch.object(
                lib_agent,
                "_call_interaction_judge",
                side_effect=[
                    ({"should_reply": True, "reply": "ok", "reason": "需要确认"}, ""),
                    ({"should_reply": False, "reply": "", "reason": "已完成"}, ""),
                ],
            ) as judge_mock, \
            patch.object(lib_agent, "_run_openclaw_message", side_effect=[_completed(), _completed()]) as run_mock, \
            patch.object(lib_agent, "_load_transcript", side_effect=transcripts):
            result = lib_agent.execute_openclaw_task(
                task=task,
                agent_id="bench-agent",
                model_id="model",
                run_id="run",
                timeout_multiplier=1.0,
                skill_dir=Path(temp_dir),
            )

        sent_messages = [call.kwargs["message"] for call in run_mock.call_args_list]
        self.assertEqual(sent_messages, ["start", "ok"])
        self.assertEqual(judge_mock.call_count, 2)
        self.assertEqual(result["interaction_events"][0]["source"], "judge_policy")
        self.assertEqual(result["interaction_events"][0]["reply"], "ok")

    def test_run_openclaw_prompt_preserves_judge_bootstrap_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_workspace = Path(temp_dir) / "agent_workspace"
            agent_workspace.mkdir()
            bootstrap = agent_workspace / "BOOTSTRAP.md"
            bootstrap.write_text("judge bootstrap", encoding="utf-8")

            with patch.object(lib_agent, "cleanup_agent_sessions"), \
                patch.object(lib_agent, "_get_agent_workspace", return_value=agent_workspace), \
                patch.object(lib_agent, "_load_transcript", return_value=([_msg("assistant", "{}")], None)), \
                patch.object(lib_agent.subprocess, "run", return_value=_completed()):
                result = lib_agent.run_openclaw_prompt(
                    agent_id="bench-judge",
                    prompt="return json",
                    workspace=Path(temp_dir) / "prompt_workspace",
                    timeout_seconds=30,
                )

            self.assertEqual(result["status"], "success")
            self.assertTrue(bootstrap.exists())
            self.assertEqual(bootstrap.read_text(encoding="utf-8"), "judge bootstrap")

    def test_extract_json_object_accepts_repeated_json_objects(self) -> None:
        text = (
            '{"should_reply": true, "reply": "ok", "reason": "需要确认"}'
            "\n\n"
            '{"should_reply": true, "reply": "ok", "reason": "重复输出"}'
        )

        parsed = lib_agent._extract_json_object(text)

        self.assertEqual(parsed["should_reply"], True)
        self.assertEqual(parsed["reply"], "ok")
        self.assertEqual(parsed["reason"], "需要确认")


if __name__ == "__main__":
    unittest.main()
