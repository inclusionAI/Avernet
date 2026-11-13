from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "undercover.py"
spec = importlib.util.spec_from_file_location("undercover_identity", SCRIPT)
assert spec and spec.loader
uc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uc)


class BcsIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"BOT_DATA_DIR": self.temp.name}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.session = {
            "id": "group:game", "group_id": "group", "participants": [
                {"bot_uuid": "host-id", "bot_name": "主持人", "actor_kind": "bot", "role": "manager"},
                {"bot_uuid": "player-id", "bot_name": "主持人", "actor_kind": "bot", "role": "worker"},
                {"bot_uuid": "human-id", "actor_kind": "human", "mode": "present"},
            ],
        }

    def cli(self, *args, **kwargs):
        if args[:2] == ("session", "get"):
            return 0, json.dumps(self.session), ""
        return 0, '{"allowed":true}', ""

    def invoke(self, *args):
        out = io.StringIO()
        with patch("sys.argv", [str(SCRIPT), *args]), contextlib.redirect_stdout(out):
            try:
                uc.main()
            except SystemExit as exc:
                self.assertIn(exc.code, (0, 2))
        return json.loads(out.getvalue())

    def test_begin_to_init_uses_delivery_id_without_local_identity(self):
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            result = self.invoke("begin", "--session", "group:game", "--referee-uuid", "host-id")
            args = shlex.split(result["init_command"])[1:]
            self.assertIn("--referee-uuid", args)
            self.assertEqual(args[args.index("--referee-uuid") + 1], "host-id")
            result = self.invoke(*args)
        self.assertTrue(result["ok"], result)
        state = uc.load_state("group:game")
        self.assertEqual(state["referee_uuid"], "host-id")
        self.assertEqual([s["bot_uuid"] for s in state["seats"] if s["kind"] == "bot"], ["player-id"])

    def test_unknown_or_worker_identity_cannot_begin(self):
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            for identity in ("new-connect-id", "player-id", "主持人"):
                result = self.invoke("begin", "--session", "group:game", "--referee-uuid", identity)
                self.assertEqual(result["error"], "REFEREE_MISMATCH")

    def test_missing_delivery_identity_does_not_use_environment(self):
        with patch.dict(os.environ, {"BCN_BOT_UUID": "host-id"}), patch.object(uc, "bcs_cli", side_effect=self.cli):
            result = self.invoke("begin", "--session", "group:game")
        self.assertFalse(result["ok"])

    def test_duplicate_display_names_keep_distinct_player_ids(self):
        self.session["participants"].append({"bot_uuid": "player-2", "bot_name": "主持人", "actor_kind": "bot", "role": "worker"})
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            result = self.invoke("begin", "--session", "group:game", "--referee-uuid", "host-id")
            result = self.invoke(*shlex.split(result["init_command"])[1:])
        self.assertTrue(result["ok"], result)
        state = uc.load_state("group:game")
        self.assertEqual({s["bot_uuid"] for s in state["seats"] if s["kind"] == "bot"}, {"player-id", "player-2"})
        self.assertEqual(self.invoke("status", "--session", "group:game")["referee_uuid"], "host-id")

    def test_member_change_between_begin_and_init_prevents_dealing(self):
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            result = self.invoke("begin", "--session", "group:game", "--referee-uuid", "host-id")
            self.session["participants"][1]["bot_uuid"] = "replacement-worker"
            result = self.invoke(*shlex.split(result["init_command"])[1:])
        self.assertEqual(result["error"], "ROSTER_MISMATCH")
        self.assertFalse(uc.state_path("group:game").exists())

    def test_failed_cli_cannot_grant_permission_from_stdout(self):
        with patch.object(uc, "bcs_cli", return_value=(1, '{"allowed":true}', "failed")) as cli:
            result = self.invoke("open-round", "--session", "group:game", "--retry")
        self.assertEqual(result["error"], "PERMISSION_UNREADABLE")
        self.assertEqual(cli.call_count, 1)

    def test_init_rejects_host_in_roster_before_writing(self):
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            result = self.invoke("init", "--session", "group:game", "--referee-uuid", "host-id", "--human", "human-id", "--bot", "主持人=host-id", "--bot", "玩家=player-id")
        self.assertEqual(result["error"], "ROSTER_MISMATCH")
        self.assertFalse(uc.state_path("group:game").exists())

    def test_cli_preserves_runtime_configuration_and_classifies_auth(self):
        for status, expected in ((401, "BCS_AUTH_FAILED"), (403, "BCS_FORBIDDEN")):
            response = subprocess.CompletedProcess([], 1, "", f"HTTP {status}: private diagnostic")
            with patch.object(uc.subprocess, "run", return_value=response) as run:
                result = self.invoke("open-round", "--session", "group:game", "--retry")
            self.assertEqual(run.call_args.args[0][:3], ["bcs-cli", "collaborate", "permission"])
            self.assertEqual(result["error"], expected)
            self.assertNotIn("private diagnostic", json.dumps(result))
            self.assertEqual(run.call_count, 1)

    def test_auth_failure_prevents_dealing(self):
        def response(command, **kwargs):
            if command[-3:] == ["session", "get", "group:game"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(self.session), "")
            return subprocess.CompletedProcess(command, 1, "", "401 Unauthorized")
        with patch.object(uc.subprocess, "run", side_effect=response):
            result = self.invoke("init", "--session", "group:game", "--referee-uuid", "host-id", "--human", "human-id", "--bot", "主持人=player-id")
        self.assertEqual(result["error"], "BCS_AUTH_FAILED")
        self.assertFalse(uc.state_path("group:game").exists())


if __name__ == "__main__":
    unittest.main()
