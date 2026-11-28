from __future__ import annotations

import json
import unittest
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"
EVIDENCE = EVIDENCE_DIR / "three-games.json"
TIMELINE = EVIDENCE_DIR / "timeline.json"
RECONNECT = EVIDENCE_DIR / "reconnect.json"
PK_CONTROLLED = EVIDENCE_DIR / "pk-controlled.json"
ABORT_BOUNDARY = EVIDENCE_DIR / "abort-boundary.json"
FORBIDDEN_KEYS = {"token", "api_key", "apikey", "authorization", "secret", "password", "word", "words"}


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys |= _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            keys |= _walk_keys(item)
    return keys


class PublicEvidenceTests(unittest.TestCase):
    """公开证据必须保持三局完成、模型来源可核对且不含秘密字段。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_three_finished_games_with_full_session_ids(self) -> None:
        summary = self.evidence["summary"]
        self.assertEqual(summary["games"], 3)
        self.assertEqual(summary["finished_games"], 3)
        self.assertEqual(summary["player_speeches"], 4)
        self.assertEqual(summary["player_votes"], 4)
        self.assertEqual(summary["player_violations"], 0)
        group = self.evidence["source"]["group_id"]
        sessions = [game["session_id"] for game in self.evidence["games"]]
        self.assertEqual(len(set(sessions)), 3)
        for session in sessions:
            self.assertTrue(session.startswith(group + ":"))

    def test_all_player_tasks_are_model_backed_and_valid(self) -> None:
        for game in self.evidence["games"]:
            self.assertTrue(game["finished"])
            self.assertEqual(game["player_task_sources"], {"llm": game["player_task_count"]})
            for round_data in game["rounds"]:
                for kind in ("speech", "vote"):
                    item = round_data[kind]
                    self.assertEqual(item["model_source"], "llm")
                    self.assertFalse(item["degraded"])
                    self.assertTrue(item["valid"])
                    self.assertIsNone(item["violation"])
                    self.assertIsInstance(item["duration_ms"], int)
                    self.assertGreaterEqual(item["duration_ms"], 0)

    def test_evidence_contains_no_secret_or_word_fields(self) -> None:
        keys = _walk_keys(self.evidence)
        self.assertEqual(keys & FORBIDDEN_KEYS, set())
        serialized = json.dumps(self.evidence, ensure_ascii=False)
        for forbidden in ("Authorization: Bearer ", "Bearer eyJ", "sk-", '"api_key"', '"token"'):
            self.assertNotIn(forbidden, serialized)

    def test_other_player_violation_notes_do_not_leak_raw_votes(self) -> None:
        for game in self.evidence["games"]:
            for item in game["violations"]["other_players"]:
                self.assertNotIn("raw", item)
                self.assertNotIn("target_actor_id", json.dumps(item, ensure_ascii=False))

    def test_stage_ordered_timeline_is_consistent_with_three_games(self) -> None:
        timeline = json.loads(TIMELINE.read_text(encoding="utf-8"))
        self.assertEqual(timeline["consistency"]["games"], 3)
        self.assertTrue(timeline["consistency"]["all_finished"])
        self.assertTrue(all(timeline["consistency"]["checks"].values()))
        self.assertEqual(timeline["consistency"]["host_retry_count"], 1)
        self.assertEqual(len(timeline["games"]), 3)
        public = {game["session_id"]: game for game in self.evidence["games"]}
        for game in timeline["games"]:
            self.assertIn(game["session_id"], public)
            self.assertTrue(game["checks"]["stage_order"])
            kinds = [event["kind"] for event in game["events"]]
            for required in ("game_start", "deal", "round_start", "speech", "speech_summary", "vote_start", "vote", "tally", "game_result"):
                self.assertIn(required, kinds)
            for round_data in public[game["session_id"]]["rounds"]:
                rnd = round_data["round"]
                speech = next(
                    event for event in game["events"]
                    if event["kind"] == "speech" and event.get("round") == rnd
                    and event.get("seat") == public[game["session_id"]]["player"]["seat"]
                )
                vote = next(
                    event for event in game["events"]
                    if event["kind"] == "vote" and event.get("round") == rnd
                    and event.get("seat") == public[game["session_id"]]["player"]["seat"]
                )
                self.assertEqual(speech["text"], round_data["speech"]["text"])
                self.assertEqual(vote["target"], round_data["vote"]["target"])
        self.assertNotIn("word", _walk_keys(timeline))
        self.assertNotIn("token", _walk_keys(timeline))
        self.assertEqual(timeline["ordering"]["kind"], "stage_order_not_strict_chronology")
        self.assertFalse(timeline["consistency"]["strict_chronology"])
        for game in timeline["games"]:
            events = game["events"]
            self.assertEqual(events[-1]["kind"], "game_result")
            for round_data in public[game["session_id"]]["rounds"]:
                rnd = round_data["round"]
                indices: dict[str, list[int]] = {}
                for index, event in enumerate(events):
                    if event.get("round") == rnd:
                        indices.setdefault(event["kind"], []).append(index)
                self.assertLess(min(indices["speech"]), max(indices["speech"]))
                self.assertLess(max(indices["speech"]), indices["speech_summary"][0])
                self.assertLess(indices["speech_summary"][0], indices["vote_start"][0])
                self.assertLess(indices["vote_start"][0], min(indices["vote"]))
                self.assertLess(max(indices["vote"]), indices["tally"][0])

    def test_controlled_bcs_pk_evidence_shows_tie_then_pk(self) -> None:
        data = json.loads(PK_CONTROLLED.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "completed")
        self.assertTrue(data["regular_tie"])
        self.assertEqual(data["regular_counts"], {"1": 2, "2": 2})
        self.assertEqual(data["pk_counts"], {"1": 1, "2": 3})
        self.assertEqual(data["output"], "PK结束，2号胜出。")
        pk_nodes = {node["node_id"] for node in data["nodes"] if node["node_id"].startswith("pk_")}
        self.assertTrue({"pk_speak_1", "pk_vote_1", "pk_tally"}.issubset(pk_nodes))
        self.assertTrue(all(node["status"] == "completed" for node in data["nodes"]))
        actual = data["actual_cpython_player"]
        self.assertEqual(actual["seat"], 1)
        received = {item["node_id"] for item in actual["received_v3_chat_send"]}
        self.assertEqual(received, {"pk_speak_1", "pk_vote_1"})
        sent = {item["node_id"]: item for item in actual["sent_v3_chat_final"]}
        self.assertEqual(sent["pk_speak_1"]["state"], "final")
        self.assertEqual(sent["pk_speak_1"]["seq"], 1)
        self.assertEqual(sent["pk_vote_1"]["state"], "final")
        self.assertEqual(sent["pk_vote_1"]["content"], "我投2号")
        self.assertNotIn("api_key", _walk_keys(data))
        self.assertNotIn("token", _walk_keys(data))
        self.assertNotIn("/Users/", json.dumps(data, ensure_ascii=False))

    def test_real_bcs_cancel_boundary_has_no_fake_abort(self) -> None:
        data = json.loads(ABORT_BOUNDARY.read_text(encoding="utf-8"))
        self.assertEqual(data["cancel_run_status"], "aborted")
        self.assertEqual(data["node_status_at_cancel"], "running")
        self.assertNotIn(
            "abort_requested",
            [event["event"] for event in data["client_events_before_release"]],
        )
        self.assertIn(
            "final_sent",
            [event["event"] for event in data["client_events_after_release"]],
        )
        self.assertEqual(data["im_bridge_probe"]["http_status"], 400)
        self.assertEqual(data["route"]["path"], "/state-machine-runs/{run_id}/cancel")
        self.assertEqual(data["cancel_http_status"], 200)
        self.assertTrue(data["permission"]["allowed"])
        actual = data["actual_cpython_player"]
        self.assertEqual({item["node_id"] for item in actual["received_v3_chat_send"]}, {"gate"})
        self.assertEqual(actual["sent_v3_chat_final"][0]["state"], "final")
        self.assertEqual(actual["sent_v3_chat_final"][0]["seq"], 1)
        self.assertNotIn("token", _walk_keys(data))
        self.assertNotIn("/Users/", json.dumps(data, ensure_ascii=False))

    def test_real_reconnect_evidence_is_redacted_and_consistent(self) -> None:
        reconnect = json.loads(RECONNECT.read_text(encoding="utf-8"))
        events = reconnect["events"]
        self.assertEqual(events[0]["event"], "connected")
        self.assertEqual(events[-1]["event"], "reconnected")
        self.assertTrue(events[-1]["same_identity"])
        serialized = json.dumps(reconnect, ensure_ascii=False)
        for forbidden in ("Bearer ey", "sk-", '"token":', '"api_key":', "/Users/"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
