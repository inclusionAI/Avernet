"""PK rules exercised through the referee commands and generated public artifacts."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_undercover_panel import SCRIPT, undercover


class TieBreakPKTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {"BOT_DATA_DIR": directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.state = json.loads((Path(__file__).parent / "fixtures/flow_baseline.json").read_text())[0]["state"]
        self.session = self.state["session_id"]
        self.state.update(phase="SPEAK_RUNNING", pending_ping=None)
        self.stage, self.attempt, self.round = "regular", 1, 1
        for seat in self.state["seats"]:
            seat["eliminated_round"] = None
        undercover.save_state(self.state)
        self.command("speeches-set", "--json", json.dumps({str(seat): "平时偶尔会遇见" for seat in range(1, 7)}))
        self.command("render-vote-run")

    def command(self, name, *args, ok=True, scoped=True):
        if scoped and name in ("speeches-set", "votes-set"):
            args = (*args, "--round", str(self.round), "--stage", self.stage, "--attempt", str(self.attempt))
        result = subprocess.run(["python3", str(SCRIPT), name, "--session", self.session, *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if ok else 2, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        if name in ("render-speak-run", "render-vote-run") and ok:
            self.stage = "pk" if data["phase"].startswith("PK_") else "regular"
            self.attempt = data["attempt"]
            self.round = data["round"]
        return data

    def vote(self, targets):
        return self.command("votes-set", "--json", json.dumps({str(seat): f"我投{target}号" if target else "我弃权" for seat, target in enumerate(targets, 1)}))

    def open_pk_vote(self, targets=(3, 3, 1, 1, 3, 1)):
        result = self.vote(targets)
        speech = self.command("render-speak-run")
        self.command("speeches-set", "--json", json.dumps({str(seat): "刚才的话可以换个角度理解" for seat in result["pk_candidates"]}))
        return speech, self.command("render-vote-run")

    def test_highest_positive_tie_starts_pk_without_elimination_or_new_round(self):
        result = self.vote([3, 3, 1, 1, 3, 1])
        self.assertEqual(result["phase"], "AWAIT_PK_SPEAK_START")
        self.assertEqual(result["pk_candidates"], [1, 3])
        self.assertIsNone(result["eliminated"])
        self.assertIsNone(result["ping"])
        status = self.command("status")
        self.assertEqual(status["round"], "1/6")
        self.assertEqual(len(status["alive"]), 6)

    def test_pk_speakers_revoters_and_next_round_keep_separate_history(self):
        self.vote([3, 3, 1, 1, 3, 1])
        speech = self.command("render-speak-run")
        self.assertEqual((speech["phase"], speech["round"], speech["attempt"]), ("PK_SPEAK_RUNNING", 1, 1))
        panel = json.loads(Path(speech["panel_params_path"]).read_text())
        self.assertEqual(panel["phase"], "pk_speaking")
        self.assertEqual(len(panel["turnOrder"]), 2)
        self.assertIsNone(panel["currentAction"])  # Human is seat 2, outside PK.
        self.assertEqual(panel["pkCandidates"], panel["turnOrder"])
        self.assertEqual(len(panel["voteHistory"]), 1)
        result = self.command("speeches-set", "--json", '{"1":"刚才那句只是补充","3":"我说的是另一种场合"}')
        self.assertEqual(result["phase"], "AWAIT_PK_VOTE_START")
        vote = self.command("render-vote-run")
        panel = json.loads(Path(vote["panel_params_path"]).read_text())
        self.assertEqual(panel["phase"], "pk_voting")
        self.assertEqual(len(panel["turnOrder"]), 6)
        self.assertEqual([c["seatNumber"] for c in panel["voteCandidates"]], [1, 3])
        self.assertEqual([len(r["speeches"]) for r in panel["publicHistory"]], [6, 2])
        self.assertIn("-pk-", panel["resultFile"])
        self.assertNotIn("作废", panel["openingAnnouncement"])
        result = self.vote([3, 1, 1, 1, 1, 1])
        self.assertEqual(result["phase"], "AWAIT_NEXT_ROUND")
        self.assertEqual(result["eliminated"]["seat"], 1)
        self.assertEqual(result["ping"]["kind"], "eulogy")
        next_round = self.command("render-speak-run")
        panel = json.loads(Path(next_round["panel_params_path"]).read_text())
        self.assertEqual((panel["round"], panel["phase"], len(panel["turnOrder"])), (2, "speaking", 5))
        self.assertEqual([r["stage"] for r in panel["voteHistory"]], ["regular", "pk"])
        run_input = json.loads(Path(next_round["input_path"]).read_text())
        self.assertEqual([r["stage"] for r in run_input["voteHistory"]], ["regular", "pk"])

    def test_pk_forbids_abstention_and_non_candidates_in_all_ballot_formats(self):
        _, vote = self.open_pk_vote()
        text = Path(vote["yaml_path"]).read_text()
        self.assertIn('"allowAbstain":false', text)
        for raw in ('我弃权', '{"kind":"vote","abstain":true}', '我投2号', self.state["human_actor_id"],
                    json.dumps({"kind": "vote", "target_actor_id": self.state["human_actor_id"]})):
            with self.subTest(raw=raw):
                parsed = self.command("parse-vote", "--voter", "4", "--text", raw)
                self.assertIsNone(parsed["target_seat"])
                self.assertNotEqual(parsed["note"], "弃权")
        result = self.vote([1, 2, 3, 4, None, None])
        self.assertEqual(result["phase"], "AWAIT_NEXT_ROUND")
        self.assertEqual(result["counts"], [])
        self.assertTrue(all(v["text"] == "无效票" for v in result["votes"]))

    def test_pk_speech_prompt_allows_defence_without_exposing_other_words(self):
        speech, _ = self.open_pk_vote((2, 1, 1, 2, 1, 2))
        text = Path(speech["yaml_path"]).read_text()
        self.assertIn("PK", text)
        self.assertIn("辩解", text)
        self.assertIn("25", text)
        self.assertNotIn("不点评别人", text)
        self.assertNotIn("别点评别人", text)
        self.assertIn('"forbidOwnWord":true', text)

    def test_stale_regular_tally_and_previous_pk_attempt_cannot_count_pk_votes(self):
        _, first = self.open_pk_vote()
        second = self.command("render-vote-run", "--retry")
        self.assertEqual(second["attempt"], 2)
        self.assertNotEqual(first["yaml_path"], second["yaml_path"])
        payload = json.dumps({str(i): "我投1号" for i in range(1, 7)})
        result = self.command("votes-set", "--json", payload, ok=False, scoped=False)
        self.assertEqual(result["error"], "WRONG_STAGE")
        result = self.command("votes-set", "--json", payload, "--stage", "pk", "--attempt", "1", ok=False, scoped=False)
        self.assertEqual(result["error"], "STALE_ATTEMPT")
        self.assertEqual(self.command("status")["phase"], "PK_VOTE_RUNNING")
        self.assertEqual(self.vote([3, 3, 1, 1, 3, 1])["phase"], "AWAIT_NEXT_ROUND")

    def test_three_way_and_all_player_ties_get_only_one_pk(self):
        for targets, count in (([2, 3, 1, 1, 2, 3], 3), ([2, 3, 4, 5, 6, 1], 6)):
            with self.subTest(count=count):
                self.state.update(phase="VOTE_RUNNING", pending_ping=None)
                undercover.save_state(self.state)
                self.stage, self.attempt, self.round = "regular", 1, 1
                speech, _ = self.open_pk_vote(targets)
                panel = json.loads(Path(speech["panel_params_path"]).read_text())
                self.assertEqual(len(panel["turnOrder"]), count)
                result = self.vote(targets)
                self.assertEqual(result["phase"], "AWAIT_NEXT_ROUND")
                self.assertIsNone(result["eliminated"])
                self.assertEqual(self.command("render-speak-run")["round"], 2)

    def test_zero_regular_votes_skip_pk(self):
        result = self.vote([None] * 6)
        self.assertEqual(result["phase"], "AWAIT_NEXT_ROUND")
        self.assertEqual(result["pk_candidates"], [])

    def test_last_round_finishes_pk_before_deciding_winner(self):
        for targets, winner in (([3, 3, 1, 3, 3, 3], "civilian"), ([3, 3, 1, 1, 3, 1], "undercover")):
            with self.subTest(winner=winner):
                self.state["config"]["max_rounds"] = 1
                self.state["phase"] = "VOTE_RUNNING"
                undercover.save_state(self.state)
                self.stage, self.attempt, self.round = "regular", 1, 1
                self.open_pk_vote()
                result = self.vote(targets)
                self.assertEqual((result["phase"], result["winner"]), ("FINISHED", winner))
                self.command("render-speak-run", ok=False)

    def test_pk_human_elimination_skips_eulogy(self):
        self.open_pk_vote((2, 1, 1, 2, 1, 2))
        result = self.vote([2, 1, 2, 2, 2, 2])
        self.assertEqual(result["eliminated"]["seat"], 2)
        self.assertIn("直接 open-round", result["next_action"])
        self.assertIsNone(self.command("status")["pending_ping"])

    def test_previous_round_pk_submission_cannot_be_replayed(self):
        self.open_pk_vote()
        self.vote([3, 3, 1, 1, 3, 1])
        self.command("render-speak-run")
        self.command("speeches-set", "--json", json.dumps({str(i): "换个场合也常遇到" for i in range(1, 7)}))
        self.command("render-vote-run")
        self.open_pk_vote()
        payload = json.dumps({str(i): "我投1号" for i in range(1, 7)})
        # Both an unscoped legacy PK command and an explicitly old round are rejected.
        result = self.command("votes-set", "--json", payload, "--stage", "pk", "--attempt", "1", ok=False, scoped=False)
        self.assertEqual(result["error"], "STALE_ROUND")
        result = self.command("votes-set", "--json", payload, "--stage", "pk", "--attempt", "1", "--round", "1", ok=False, scoped=False)
        self.assertEqual(result["error"], "STALE_ROUND")
        self.assertEqual(self.command("status")["phase"], "PK_VOTE_RUNNING")


if __name__ == "__main__":
    unittest.main()
