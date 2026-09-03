from __future__ import annotations

import importlib.util
import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "undercover.py"
spec = importlib.util.spec_from_file_location("undercover", SCRIPT)
assert spec and spec.loader
undercover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(undercover)


class UndercoverPanelParamsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"BOT_DATA_DIR": self.temp_dir.name}, clear=False)
        self.environment.start()
        self.state = {
            "session_id": "group-game:session-1",
            "group_id": "group-game",
            "human_actor_id": "human-1",
            "referee_uuid": "referee-1",
            "phase": "AWAIT_START",
            "config": {"max_rounds": 6},
            "words": {"civilian": "secret-civilian-word", "undercover": "secret-undercover-word"},
            "round": 0,
            "rounds": [],
            "seats": [
                {"seat": 1, "kind": "human", "display": "你", "bot_uuid": None, "binding": None, "word": "secret-civilian-word", "role": "civilian", "alive": True},
                {"seat": 2, "kind": "bot", "display": "甲", "bot_uuid": "bot-a", "binding": "seat2", "word": "secret-civilian-word", "role": "civilian", "alive": True},
                {"seat": 3, "kind": "bot", "display": "乙", "bot_uuid": "bot-b", "binding": "seat3", "word": "secret-undercover-word", "role": "undercover", "alive": True},
            ],
        }

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_dir.cleanup()

    def test_projection_is_whitelisted_and_vote_candidates_exclude_viewer(self) -> None:
        speech = undercover.public_panel_projection(self.state, "speak", 1)
        vote = undercover.public_panel_projection(self.state, "vote", 2)
        payload = json.dumps({"speech": speech, "vote": vote}, ensure_ascii=False)

        self.assertEqual(speech["runId"], "{{bcs.run_id}}")
        self.assertEqual(speech["apiBaseUrl"], "/bcnproxy")
        self.assertEqual(speech["groupId"], "group-game")
        self.assertEqual(speech["seatOrder"], ["human-1", "bot-a", "bot-b"])
        self.assertEqual(speech["nodeActorMap"]["speak_1"], "human-1")
        self.assertEqual(speech["currentAction"], {"actorId": "human-1", "type": "speech", "nodeId": "speak_1"})
        self.assertEqual([candidate["actorId"] for candidate in vote["voteCandidates"]], ["bot-a", "bot-b"])
        self.assertEqual(vote["currentAction"]["nodeId"], "vote_1")
        for forbidden in ("role", "word", "secret-civilian-word", "secret-undercover-word", "raw", "reasoning", "target"):
            self.assertNotIn(forbidden, payload)

    def test_vote_and_retry_get_distinct_phase_scoped_panel_tabs(self) -> None:
        self.state["round"] = 1
        self.state["rounds"] = [{"round": 1, "order": [1, 2, 3], "speeches": {}, "votes": {}, "counts": {}, "renders": {}}]
        self.state["phase"] = "AWAIT_VOTE_START"
        undercover.save_state(self.state)

        first_vote = undercover.prepare_vote_run(self.state["session_id"], retry=False)
        first_params = json.loads(Path(first_vote["panel_params_path"]).read_text(encoding="utf-8"))
        saved = undercover.load_state(self.state["session_id"])
        self.assertEqual(first_params["phase"], "voting")
        self.assertEqual(first_params["round"], 1)
        self.assertEqual([candidate["actorId"] for candidate in first_params["voteCandidates"]], ["bot-a", "bot-b"])
        self.assertIn("-vote-r1-a1-{{bcs.run_id}}", first_vote["panel_tab"]["id"])

        self.assertEqual(saved["phase"], "VOTE_RUNNING")
        retry_vote = undercover.prepare_vote_run(self.state["session_id"], retry=True)
        retry_params = json.loads(Path(retry_vote["panel_params_path"]).read_text(encoding="utf-8"))
        self.assertEqual(retry_params["attempt"], 2)
        self.assertIn("-vote-r1-a2-{{bcs.run_id}}", retry_vote["panel_tab"]["id"])
        self.assertNotEqual(first_vote["panel_tab"]["id"], retry_vote["panel_tab"]["id"])
        self.assertNotEqual(first_vote["panel_params_path"], retry_vote["panel_params_path"])

    def test_phase_files_and_actual_submission_have_identical_panel_metadata(self) -> None:
        undercover.save_state(self.state)
        payload = undercover.prepare_speak_run(self.state["session_id"], retry=False)
        params = json.loads(Path(payload["panel_params_path"]).read_text(encoding="utf-8"))
        command = shlex.split(payload["run_command"])
        self.assertIn("--panel-component", command)
        self.assertEqual(command[command.index("--panel-component") + 1], undercover.PANEL_COMPONENT)
        self.assertEqual(command[command.index("--panel-params") + 1], f"@{payload['panel_params_path']}")
        self.assertEqual(params["phase"], "speaking")
        self.assertIn("-speak-r1-a1-{{bcs.run_id}}", payload["panel_tab"]["id"])

        with patch.object(undercover, "bcs_cli", return_value=(0, '{"run_id":"run-created"}', "")) as bcs_cli:
            result = undercover.submit_run(
                self.state["session_id"], payload["yaml_path"], payload["input_path"],
                payload["panel_params_path"], payload["panel_tab"], payload["bindings"],
            )
        self.assertEqual(result["run_id"], "run-created")
        submitted = list(bcs_cli.call_args.args)
        expected = command[1:]  # display command begins with the shell helper `bcs`.
        self.assertEqual(submitted, expected)


if __name__ == "__main__":
    unittest.main()
