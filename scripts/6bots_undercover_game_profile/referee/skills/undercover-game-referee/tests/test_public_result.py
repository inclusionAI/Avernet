from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_undercover_panel as fixtures

uc = fixtures.undercover


class PublicResultTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.UndercoverPanelParamsTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture.state
        self.state.update(pending_ping=None, phase="FINISHED", result={"winner": "civilian", "reason": "卧底已经全部出局"})
        rnd = self.state["rounds"][0]
        rnd.update(renders={"vote": 2}, result_file="undercover-result-v1-r1-a2.json", eliminated=3, tie=False)
        for seat in self.state["seats"]:
            seat["eliminated_round"] = None if seat["alive"] else 1
        uc.save_state(self.state)
        self.remote = None
        self.result_file = "undercover-result-v1-r1-a2.json"
        self.uploads = 0
        self.lose_response = False
        self.close_fails = False
        self.cli = patch.object(uc, "bcs_cli", side_effect=self.call_cli)
        self.cli.start()
        self.addCleanup(self.cli.stop)

    def metadata(self):
        return {"file_id": "result-file", "file_name": self.result_file,
                "session_id": self.state["session_id"], "status": "Ready",
                "owner": {"actor_kind": "Bot", "actor_id": "referee-1"}}

    def call_cli(self, *args, **kwargs):
        if args[2] == "complete":
            return (1, "", "unavailable") if self.close_fails else (0, '{"status":"completed"}', "")
        action = args[3]
        if action == "list":
            return 0, json.dumps({"items": [self.metadata()] if self.remote else [], "total": int(bool(self.remote))}), ""
        if action == "upload":
            self.uploads += 1
            self.remote = json.loads(Path(args[args.index("--path") + 1]).read_text())
            if self.lose_response:
                self.lose_response = False
                return 1, "", "lost response"
            return 0, json.dumps(self.metadata()), ""
        if action == "download":
            Path(args[args.index("--out") + 1]).write_text(json.dumps(self.remote))
            return 0, "{}", ""
        self.fail(f"unexpected CLI action {action}")

    def invoke(self, command):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                command(argparse.Namespace(session=self.state["session_id"], full=False))
            except SystemExit as exc:
                self.assertEqual(exc.code, 2)
        return json.loads(out.getvalue())

    def test_reveal_publishes_fact_result_and_finish_does_not_upload_again(self):
        self.assertEqual(self.invoke(uc.cmd_reveal)["winner"], "civilian")
        self.assertEqual(self.remote["gameSessionId"], self.state["session_id"])
        self.assertEqual(self.remote["hostActorId"], "referee-1")
        self.assertEqual(self.remote["attempt"], 2)
        self.assertEqual(self.remote["winner"], "civilian")
        self.assertIn("secret-undercover-word", self.remote["summary"])
        self.assertNotIn("private raw", json.dumps(self.remote))
        self.assertNotIn("raw vote", json.dumps(self.remote))
        self.assertTrue(self.invoke(uc.cmd_finish)["completed"])
        self.assertTrue(self.invoke(uc.cmd_publish_result)["published"])
        self.assertEqual(self.uploads, 1)
        self.assertEqual(self.invoke(uc.cmd_reveal)["error"], "ALREADY_REVEALED")

    def test_lost_response_reconciles_remote_file_before_retry(self):
        self.lose_response = True
        self.assertEqual(self.invoke(uc.cmd_reveal)["error"], "RESULT_PUBLISH_FAILED")
        self.assertNotIn("revealed_at", uc.load_state(self.state["session_id"]))
        status = self.invoke(uc.cmd_status)
        self.assertIn("尚未确认发布及揭晓", status["next_action"])
        self.assertNotIn("secret-undercover-word", json.dumps(status))
        self.assertEqual(self.invoke(uc.cmd_reveal)["winner"], "civilian")
        self.assertEqual(self.uploads, 1)

    def test_conflicting_result_is_not_overwritten(self):
        self.remote = {"winner": "undercover"}
        self.assertEqual(self.invoke(uc.cmd_reveal)["error"], "RESULT_PUBLISH_FAILED")
        self.assertEqual(self.uploads, 0)
        self.assertNotIn("revealed_at", uc.load_state(self.state["session_id"]))

    def test_game_and_reveal_are_required_before_publication(self):
        self.assertEqual(self.invoke(uc.cmd_publish_result)["error"], "NOT_REVEALED")
        self.state["phase"] = "VOTE_RUNNING"
        uc.save_state(self.state)
        self.assertEqual(self.invoke(uc.cmd_reveal)["error"], "NOT_FINISHED")
        self.assertEqual(self.uploads, 0)

    def test_close_failure_preserves_public_result(self):
        self.invoke(uc.cmd_reveal)
        self.close_fails = True
        before = uc.load_state(self.state["session_id"])
        self.assertEqual(self.invoke(uc.cmd_finish)["error"], "FINISH_FAILED")
        self.assertEqual(uc.load_state(self.state["session_id"]), before)
        self.close_fails = False
        self.assertTrue(self.invoke(uc.cmd_finish)["completed"])
        self.assertEqual(self.uploads, 1)

    def test_pk_finale_publishes_both_ballots_under_pk_result_identity(self):
        rnd = self.state["rounds"][0]
        rnd.update(tallied=True, votes={"1": {"display": "我投3号", "target": 3}}, counts={"3": 1}, tie=True)
        self.result_file = "undercover-result-v1-r1-pk-a1.json"
        rnd["pk"] = {"order": [2, 3], "speeches": {"2": {"display": "补充描述"}},
                     "votes": {"1": {"display": "我投2号", "target": 2}}, "counts": {"2": 1},
                     "tallied": True, "tie": False, "renders": {"vote": 1}, "result_file": self.result_file}
        uc.save_state(self.state)
        result = self.invoke(uc.cmd_reveal)
        self.assertEqual(result["rounds"][0]["pk"]["votes"], {"1": 2})
        self.assertEqual((self.remote["stage"], self.remote["attempt"]), ("pk", 1))
        self.assertIn("常规投票", self.remote["summary"])
        self.assertIn("PK 投票", self.remote["summary"])
        self.assertIn("我投3号", self.remote["summary"])
        self.assertIn("我投2号", self.remote["summary"])
        self.assertTrue(self.invoke(uc.cmd_finish)["completed"])


if __name__ == "__main__":
    unittest.main()
