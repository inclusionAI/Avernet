"""Automatic recovery from a StateMachine RunResult (issue #2610)."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "undercover.py"
spec = importlib.util.spec_from_file_location("undercover", SCRIPT)
assert spec and spec.loader
undercover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(undercover)


class RunResultRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"BOT_DATA_DIR": self.temp_dir.name}, clear=False)
        self.environment.start()
        self.state = {
            "session_id": "group-game:session-1", "group_id": "group-game",
            "human_actor_id": "human-1", "referee_uuid": "referee-1",
            "phase": "SPEAK_RUNNING", "config": {"max_rounds": 6}, "pending_ping": None, "result": None,
            "words": {"civilian": "secret-civilian-word", "undercover": "secret-undercover-word"},
            "round": 1,
            "rounds": [{"round": 1, "order": [1, 2, 3], "speeches": {}, "votes": {}, "counts": {},
                        "eliminated": None, "tie": False, "renders": {"speak": 1}}],
            "seats": [
                {"seat": 1, "kind": "human", "display": "你", "bot_uuid": None, "binding": None, "word": "secret-civilian-word", "role": "civilian", "alive": True},
                {"seat": 2, "kind": "bot", "display": "甲", "bot_uuid": "bot-a", "binding": "seat2", "word": "secret-civilian-word", "role": "civilian", "alive": True},
                {"seat": 3, "kind": "bot", "display": "乙", "bot_uuid": "bot-b", "binding": "seat3", "word": "secret-undercover-word", "role": "undercover", "alive": True},
            ],
        }

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_dir.cleanup()

    def save(self, phase: str, **stage: object) -> None:
        self.state["phase"] = phase
        self.state["rounds"][0].update(stage)
        undercover.save_state(self.state)

    def failure(self, call) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            call()
        self.assertEqual(raised.exception.code, 2)
        return json.loads(out.getvalue())

    def output(self, call) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            call()
        return json.loads(out.getvalue())

    def test_third_reopen_of_the_same_stage_is_refused(self) -> None:
        self.save("SPEAK_RUNNING", renders={"speak": 2})
        payload = undercover.prepare_speak_run(self.state["session_id"], retry=True)
        self.assertEqual(payload["attempt"], 3)
        error = self.failure(lambda: undercover.prepare_speak_run(self.state["session_id"], retry=True))
        self.assertEqual(error["error"], "RETRY_LIMIT")

    def test_user_abort_is_never_reopened(self) -> None:
        self.save("VOTE_RUNNING", renders={"speak": 1, "vote": 1})
        error = self.failure(lambda: undercover.prepare_vote_run(
            self.state["session_id"], retry=True, failure_reason="user_aborted"))
        self.assertEqual(error["error"], "USER_ABORTED")
        self.assertEqual(undercover.load_state(self.state["session_id"])["rounds"][0]["renders"]["vote"], 1)

    def test_result_of_a_replaced_run_is_ignored(self) -> None:
        self.save("VOTE_RUNNING", renders={"speak": 1, "vote": 1}, run_ids={"vote": "run-new"})
        error = self.failure(lambda: undercover.prepare_vote_run(self.state["session_id"], retry=True, run_id="run-old"))
        self.assertEqual(error["error"], "STALE_RUN")
        payload = undercover.prepare_vote_run(self.state["session_id"], retry=True, run_id="run-new")
        self.assertEqual(payload["attempt"], 2)

    def test_submitted_run_id_reads_the_cli_run_object(self) -> None:
        self.assertEqual(undercover.submitted_run_id({"run": {"run_id": "sm-1"}, "nodes": []}), "sm-1")
        self.assertEqual(undercover.submitted_run_id({"run_id": "sm-2"}), "sm-2")
        self.assertEqual(undercover.submitted_run_id({"nodes": [{"run_id": "sm-3"}]}), "sm-3")
        self.assertIsNone(undercover.submitted_run_id({}))

    def test_status_reports_current_attempt_and_run(self) -> None:
        self.save("VOTE_RUNNING", renders={"speak": 1, "vote": 2}, run_ids={"vote": "sm-2"})
        status = self.output(lambda: undercover.cmd_status(
            argparse.Namespace(session=self.state["session_id"], full=False)))
        self.assertEqual((status["attempt"], status["run_id"]), (2, "sm-2"))

    def test_recap_repeats_public_speeches_without_words(self) -> None:
        speeches = {"1": {"raw": "private raw", "display": "公开甲", "violation": None},
                    "2": {"raw": "secret-civilian-word raw", "display": "公开乙", "violation": None}}
        self.save("AWAIT_VOTE_START", speeches=speeches)
        recap = self.output(lambda: undercover.cmd_recap(argparse.Namespace(session=self.state["session_id"])))
        self.assertEqual([item["text"] for item in recap["speeches"]], ["公开甲", "公开乙"])
        text = json.dumps(recap, ensure_ascii=False)
        for forbidden in ("private raw", "secret-civilian-word", "secret-undercover-word", "civilian", "undercover"):
            self.assertNotIn(forbidden, text)

    def test_recap_refuses_a_running_stage(self) -> None:
        self.save("SPEAK_RUNNING")
        error = self.failure(lambda: undercover.cmd_recap(argparse.Namespace(session=self.state["session_id"])))
        self.assertEqual(error["error"], "WRONG_PHASE")

    def recover_args(self, reason: str = "node_failed") -> argparse.Namespace:
        return argparse.Namespace(session=self.state["session_id"], run_id="sm-1", failure_reason=reason)

    def test_recover_refuses_a_user_abort(self) -> None:
        self.save("SPEAK_RUNNING")
        error = self.failure(lambda: undercover.cmd_recover(self.recover_args("user_aborted")))
        self.assertEqual(error["error"], "USER_ABORTED")

    def test_recover_reopens_the_running_stage_in_one_call(self) -> None:
        calls = []
        for phase, command in (("SPEAK_RUNNING", "cmd_open_round"), ("PK_VOTE_RUNNING", "cmd_open_vote")):
            self.save(phase)
            with patch.object(undercover, command, side_effect=lambda args: calls.append(args)):
                undercover.cmd_recover(self.recover_args())
        self.assertEqual([(a.retry, a.run_id, a.failure_reason) for a in calls],
                         [(True, "sm-1", "node_failed")] * 2)

    def test_recover_recaps_a_failed_collect_and_ignores_other_phases(self) -> None:
        self.save("AWAIT_VOTE_START", speeches={"1": {"raw": "private raw", "display": "公开甲", "violation": None}})
        recap = self.output(lambda: undercover.cmd_recover(self.recover_args()))
        self.assertEqual(recap["speeches"][0]["text"], "公开甲")
        self.save("AWAIT_START")
        idle = self.output(lambda: undercover.cmd_recover(self.recover_args()))
        self.assertFalse(idle["recovered"])


if __name__ == "__main__":
    unittest.main()
