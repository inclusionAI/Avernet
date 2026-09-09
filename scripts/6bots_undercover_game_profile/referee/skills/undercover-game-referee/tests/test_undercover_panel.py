from __future__ import annotations

import importlib.util
import json
import os
import re
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
            "session_id": "group-game:session-1", "group_id": "group-game",
            "human_actor_id": "human-1", "referee_uuid": "referee-1",
            "phase": "AWAIT_START", "config": {"max_rounds": 6},
            "words": {"civilian": "secret-civilian-word", "undercover": "secret-undercover-word"},
            "round": 1,
            "rounds": [{"round": 1, "order": [1, 2, 3], "speeches": {
                "1": {"raw": "private raw", "display": "公开甲", "violation": None},
                "2": {"raw": "private raw 2", "display": "公开乙", "violation": None},
            }, "votes": {"1": {"raw": "raw vote", "target": 2}}, "counts": {}, "renders": {}}],
            "seats": [
                {"seat": 1, "kind": "human", "display": "你", "bot_uuid": None, "binding": None, "word": "secret-civilian-word", "role": "civilian", "alive": True},
                {"seat": 2, "kind": "bot", "display": "甲", "bot_uuid": "bot-a", "binding": "seat2", "word": "secret-civilian-word", "role": "civilian", "alive": True},
                {"seat": 3, "kind": "bot", "display": "乙", "bot_uuid": "bot-b", "binding": "seat3", "word": "secret-undercover-word", "role": "undercover", "alive": False},
            ],
        }

    def tearDown(self) -> None:
        self.environment.stop(); self.temp_dir.cleanup()

    def test_projection_has_stable_roster_turn_order_history_host_and_privacy(self) -> None:
        speech = undercover.public_panel_projection(self.state, "speak", 2)
        vote = undercover.public_panel_projection(self.state, "vote", 2)
        payload = json.dumps({"speech": speech, "vote": vote}, ensure_ascii=False)
        self.assertEqual(speech["runId"], "{{bcs.run_id}}")
        for projection in (speech, vote):
            self.assertNotIn("apiBaseUrl", projection)
            self.assertNotIn("baseUrl", projection)
        self.assertEqual(speech["seatOrder"], ["human-1", "bot-a", "bot-b"])
        self.assertEqual(speech["turnOrder"], ["human-1", "bot-a"])
        self.assertEqual([p["seatNumber"] for p in speech["players"]], [1, 2, 3])
        self.assertTrue(speech["players"][2]["eliminated"])
        self.assertEqual(speech["nodeActorMap"], {
            "speak_1": "human-1", "speak_2": "bot-a",
            "collect": "referee-1",
        })
        self.assertEqual(vote["nodeActorMap"], {
            "vote_1": "human-1", "vote_2": "bot-a",
            "tally": "referee-1",
        })
        player_by_id = {player["actorId"]: player for player in speech["players"]}
        self.assertEqual(
            [(actor_id, player_by_id[actor_id]["seatNumber"], player_by_id[actor_id]["displayName"])
             for actor_id in speech["turnOrder"]],
            [("human-1", 1, "你"), ("bot-a", 2, "甲")],
        )
        self.assertEqual([(c["actorId"], c["seatNumber"]) for c in vote["voteCandidates"]], [("bot-a", 2)])
        self.assertEqual(speech["publicHistory"][0]["speeches"][0]["text"], "公开甲")
        self.assertEqual(speech["rules"]["speechMaxChars"], undercover.SPEECH_MAX_CHARS)
        for forbidden in ("role", "word", "secret-civilian-word", "secret-undercover-word", "private raw", "raw vote", '"target"'):
            self.assertNotIn(forbidden, payload)

    def test_stable_non_closable_tab_reuses_identity_but_not_run_files(self) -> None:
        self.state["phase"] = "AWAIT_VOTE_START"; undercover.save_state(self.state)
        first = undercover.prepare_vote_run(self.state["session_id"], retry=False)
        second = undercover.prepare_vote_run(self.state["session_id"], retry=True)
        self.assertEqual(first["panel_tab"]["id"], second["panel_tab"]["id"])
        self.assertEqual(first["panel_tab"]["id"], "undercover-game-group-game-session-1")
        self.assertNotEqual(first["panel_params_path"], second["panel_params_path"])
        for payload in (first, second):
            command = shlex.split(payload["run_command"])
            self.assertEqual(command[command.index("--panel-tab-closable") + 1], "false")
            self.assertEqual(command[command.index("--panel-component") + 1], "bcsPanel.UndercoverGamePanel")

    def test_submission_command_matches_rendered_metadata(self) -> None:
        self.state["phase"] = "AWAIT_START"; self.state["round"] = 0; self.state["rounds"] = []
        undercover.save_state(self.state)
        payload = undercover.prepare_speak_run(self.state["session_id"], retry=False)
        command = shlex.split(payload["run_command"])
        with patch.object(undercover, "bcs_cli", return_value=(0, '{"run_id":"run-created"}', "")) as bcs_cli:
            result = undercover.submit_run(self.state["session_id"], payload["yaml_path"], payload["input_path"], payload["panel_params_path"], payload["panel_tab"], payload["bindings"])
        self.assertEqual(command[command.index("--panel-component") + 1], "bcsPanel.UndercoverGamePanel")
        self.assertEqual(command[command.index("--session") + 1], self.state["session_id"])
        self.assertEqual(result["run_id"], "run-created")
        self.assertEqual(list(bcs_cli.call_args.args), command[1:])

    def _ui_contexts(self, yaml_text: str) -> list[dict[str, object]]:
        pattern = re.escape(undercover.UI_CONTEXT_OPEN) + r"\n\s*(.*?)\n\s*" + re.escape(undercover.UI_CONTEXT_CLOSE)
        return [json.loads(item.strip()) for item in re.findall(pattern, yaml_text)]

    def test_human_yaml_context_is_versioned_private_with_direct_player_entry(self) -> None:
        speech, _ = undercover.render_speak_yaml(self.state)
        vote, _ = undercover.render_vote_yaml(self.state)
        speech_contexts = self._ui_contexts(speech); vote_contexts = self._ui_contexts(vote)
        self.assertEqual(speech_contexts, [{"action": "speech", "round": 1, "seatNumber": 1, "word": "secret-civilian-word", "maxChars": 25, "forbidOwnWord": True, "bluntness": 4}])
        self.assertEqual(vote_contexts, [{"action": "vote", "round": 1, "seatNumber": 1, "word": "secret-civilian-word", "allowAbstain": True}])
        self.assertEqual(speech.count("secret-undercover-word"), 0)  # eliminated Bot has no node
        self.assertGreaterEqual(speech.count("secret-civilian-word"), 2)
        self.assertIn("targets: [speak_2, collect]", speech)
        self.assertEqual(vote.count("targets: [tally]"), 2)
        self.assertNotIn(undercover.UI_CONTEXT_OPEN, speech.split("speak_2:", 1)[1])

    def test_structured_and_legacy_votes(self) -> None:
        self.assertEqual(undercover.parse_vote(self.state, 1, '{"kind":"vote","target_actor_id":"bot-a"}'), (2, None))
        self.assertEqual(undercover.parse_vote(self.state, 1, '{"kind":"vote","abstain":true}'), (None, "弃权"))
        for text, reason in [
            ('{"kind":"vote","target_actor_id":"unknown"}', "结构化投票目标无效"),
            ('{"kind":"vote","target_actor_id":"bot-b"}', "结构化投票目标无效"),
            ('{"kind":"vote","target_actor_id":"human-1"}', "投了自己"),
            ('{"kind":"vote","target_actor_id":"bot-a"', "结构化投票格式错误"),
            ('{"kind":"vote","target_actor_id":"unknown","note":"我投2号"}', "结构化投票格式错误"),
        ]:
            self.assertEqual(undercover.parse_vote(self.state, 1, text), (None, reason))
        self.assertEqual(undercover.parse_vote(self.state, 1, "我投2号"), (2, None))
        self.assertEqual(undercover.parse_vote(self.state, 1, "我投甲"), (2, None))


if __name__ == "__main__":
    unittest.main()
