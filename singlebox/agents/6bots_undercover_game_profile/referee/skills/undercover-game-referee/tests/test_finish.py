from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "undercover.py"
spec = importlib.util.spec_from_file_location("undercover_finish", SCRIPT)
assert spec and spec.loader
uc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uc)


class FinishTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"BOT_DATA_DIR": self.temp.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.state = {"session_id": "group:game", "phase": "FINISHED", "revealed_at": "2026-09-09"}
        uc.save_state(self.state)

    def invoke(self, *args):
        out = io.StringIO()
        with patch("sys.argv", [str(SCRIPT), "finish", *args]), contextlib.redirect_stdout(out):
            try:
                uc.main()
            except SystemExit as exc:
                self.assertEqual(exc.code, 2)
        return json.loads(out.getvalue())

    def test_environment_precedence_and_preserved_credentials(self):
        for supplied, expected in [({}, "/test-home/.openclaw"),
                                   ({"OPENCLAW_DATA_DIR": "/runtime"}, "/runtime"),
                                   ({"BOT_DATA_DIR": "/bot", "OPENCLAW_DATA_DIR": "/other"}, "/bot")]:
            with self.subTest(supplied=supplied), patch.dict(os.environ, supplied | {"BCN_BOT_TOKEN": "synthetic"}, clear=True), patch.object(Path, "home", return_value=Path("/test-home")):
                env = uc._bcs_env()
                self.assertEqual(env["BOT_DATA_DIR"], expected)
                self.assertEqual(env["BCN_BOT_TOKEN"], "synthetic")
                self.assertEqual(dict(os.environ), supplied | {"BCN_BOT_TOKEN": "synthetic"})

    def test_finish_uses_wrapper_environment_and_can_repeat(self):
        with patch.dict(os.environ, {"OPENCLAW_DATA_DIR": self.temp.name}, clear=True), patch.object(uc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"already_completed":true}', "")) as run:
            for _ in range(2):
                self.assertTrue(self.invoke("--session", "group:game")["completed"])
            self.assertEqual(run.call_args.args[0], ["bcs-cli", "--json", "session", "complete", "group:game"])
            self.assertEqual(run.call_args.kwargs["env"]["BOT_DATA_DIR"], self.temp.name)
        self.assertEqual(uc.load_state("group:game"), self.state)

    def test_finish_requests_json_from_text_default_cli(self):
        def cli_run(cmd, **kwargs):
            output = '{"status":"completed"}' if "--json" in cmd else "✓ Completed: group:game status=completed"
            return subprocess.CompletedProcess(cmd, 0, output, "")

        with patch.object(uc.subprocess, "run", side_effect=cli_run):
            self.assertTrue(self.invoke("--session", "group:game").get("completed"))
        self.assertEqual(uc.load_state("group:game"), self.state)

    def test_failures_propagate_and_retry_preserves_reveal(self):
        for message, error in [("401 Unauthorized", "BCS_AUTH_FAILED"), ("403 Forbidden", "BCS_FORBIDDEN"), ("connection refused", "FINISH_FAILED")]:
            with self.subTest(message=message), patch.object(uc.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", message)):
                self.assertEqual(self.invoke("--session", "group:game")["error"], error)
            self.assertEqual(uc.load_state("group:game"), self.state)
        with patch.object(uc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"status":"completed"}', "")):
            self.assertTrue(self.invoke("--session", "group:game")["completed"])

    def test_invalid_success_output_does_not_confirm_completion(self):
        with patch.object(uc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "invalid response", "")):
            self.assertEqual(self.invoke("--session", "group:game")["error"], "FINISH_FAILED")
        self.assertEqual(uc.load_state("group:game"), self.state)

    def test_finish_requires_game_reveal_and_session(self):
        with patch.object(uc, "bcs_cli") as cli:
            self.assertEqual(self.invoke()["error"], "NO_SESSION")
            for state, error in [({**self.state, "phase": "VOTE_RUNNING"}, "NOT_FINISHED"),
                                 ({**self.state, "revealed_at": None}, "NOT_REVEALED")]:
                uc.save_state(state)
                self.assertEqual(self.invoke("--session", "group:game")["error"], error)
            cli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
