"""Replay Human ballots through parsing and persisted tallying."""
from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_undercover_panel import undercover


class VoteActorIdsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = json.loads((Path(__file__).parent / "fixtures" / "flow_baseline.json").read_text())[0]["state"]
        self.state["phase"] = "VOTE_RUNNING"
        self.state["human_actor_id"] = "human-5"
        for seat in self.state["seats"]:
            seat["kind"] = "human" if seat["seat"] == 4 else "bot"
            seat["bot_uuid"] = f"bot-{seat['seat']}"
            seat["alive"] = True
        self.state["seats"][4]["bot_uuid"] = "20260908_4jh4xpc1:EBUQ5NesXHHr"
        self.state["seats"][5]["bot_uuid"] = "20260908_smefvkwb:EBUQ5NesXHHr"
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        environment = patch.dict(os.environ, {"BOT_DATA_DIR": temp.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_bare_ids_take_precedence_over_text_and_digits(self) -> None:
        for target in (5, 6):
            raw = self.state["seats"][target - 1]["bot_uuid"]
            with self.subTest(target=target):
                self.assertEqual(undercover.parse_vote(self.state, 4, f" {raw}\n"), (target, None))
        self.state["seats"][5]["bot_uuid"] = "弃权-我投5号"
        self.assertEqual(undercover.parse_vote(self.state, 4, "弃权-我投5号"), (6, None))

    def test_illegal_ids_never_fall_back_to_digits(self) -> None:
        self.state["seats"][5]["alive"] = False
        for raw, reason in (
            ("human-5", "投了自己"),
            (self.state["seats"][5]["bot_uuid"], "投票目标已出局"),
            ("20260908_unknown:EBUQ5NesXHHr", "投票目标无效"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(undercover.parse_vote(self.state, 4, raw), (None, reason))

    def test_votes_set_persists_correct_human_target_and_count(self) -> None:
        for target in (5, 6):
            actor_id = self.state["seats"][target - 1]["bot_uuid"]
            for raw in (actor_id, json.dumps({"kind": "vote", "target_actor_id": actor_id})):
                with self.subTest(target=target, raw=raw):
                    undercover.save_state(copy.deepcopy(self.state))
                    payload = {str(seat): "我弃权" for seat in self.state["rounds"][0]["order"]}
                    payload["4"] = raw
                    with patch.object(undercover, "emit"):
                        undercover.cmd_votes_set(argparse.Namespace(session=self.state["session_id"], json=json.dumps(payload)))
                    rnd = undercover.load_state(self.state["session_id"])["rounds"][0]
                    self.assertEqual(rnd["votes"]["4"]["raw"], raw)
                    self.assertEqual(rnd["votes"]["4"]["target"], target)
                    self.assertEqual(rnd["votes"]["4"]["display"], f"我投{target}号")
                    self.assertIsNone(rnd["votes"]["4"]["note"])
                    self.assertEqual(rnd["counts"], {str(target): 1})

    def test_votes_set_does_not_count_illegal_ids_or_abstention(self) -> None:
        self.state["seats"][5]["alive"] = False
        self.state["rounds"][0]["order"].remove(6)
        for raw, display in (
            ("human-5", "无效票"),
            (self.state["seats"][5]["bot_uuid"], "无效票"),
            ("20260908_unknown:EBUQ5NesXHHr", "无效票"),
            ('{"kind":"vote","abstain":true}', "我弃权"),
        ):
            with self.subTest(raw=raw):
                undercover.save_state(copy.deepcopy(self.state))
                payload = {str(seat): "我弃权" for seat in self.state["rounds"][0]["order"]}
                payload["4"] = raw
                with patch.object(undercover, "emit"):
                    undercover.cmd_votes_set(argparse.Namespace(session=self.state["session_id"], json=json.dumps(payload)))
                rnd = undercover.load_state(self.state["session_id"])["rounds"][0]
                self.assertIsNone(rnd["votes"]["4"]["target"])
                self.assertEqual(rnd["votes"]["4"]["display"], display)
                self.assertEqual(rnd["counts"], {})

    def test_legacy_seat_numbers_names_and_abstention_still_work(self) -> None:
        for raw in ("5", "我投5号", "投五号", self.state["seats"][4]["display"]):
            with self.subTest(raw=raw):
                self.assertEqual(undercover.parse_vote(self.state, 4, raw), (5, None))
        self.assertEqual(undercover.parse_vote(self.state, 4, "我弃权"), (None, "弃权"))


if __name__ == "__main__":
    unittest.main()
