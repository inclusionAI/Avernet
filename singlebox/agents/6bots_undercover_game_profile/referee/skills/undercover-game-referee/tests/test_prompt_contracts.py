"""Protect the existing run/UI contract while editing Bot instructions."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_undercover_panel import undercover

FIXTURES = Path(__file__).parent / "fixtures" / "flow_baseline.json"
PROFILES = Path(__file__).parents[4]


def without_bot_instructions(text: str) -> str:
    # Text snapshot of this renderer's literal block format. Human instructions,
    # including private UI context, are intentionally retained byte for byte.
    return re.sub(
        r"(?m)(^      \w+:\n        kind: bot_task\n[\s\S]*?        instruction: \|\n)(?:          .*\n|\n)*",
        lambda match: match[1] + "          <BOT_INSTRUCTION>\n",
        text,
    )


class PromptContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = json.loads(FIXTURES.read_text())
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.environment = patch.dict(os.environ, {"BOT_DATA_DIR": self.temp_dir.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_full_run_and_panel_snapshots_preserve_game_flow(self) -> None:
        for fixture in self.fixtures:
            for kind, render in (("speak", undercover.render_speak_yaml), ("vote", undercover.render_vote_yaml)):
                with self.subTest(round=fixture["state"]["round"], kind=kind):
                    state = fixture["state"]
                    text, bindings = render(state)
                    expected = fixture[kind]
                    self.assertEqual(without_bot_instructions(text), expected["yaml"])
                    self.assertEqual(bindings, expected["bindings"])
                    self.assertEqual(undercover.public_panel_projection(state, kind, 1), expected["panel"])
                    self.assertEqual(undercover.panel_tab_metadata(state, kind, 1), expected["tab"])
                    self.assertLess(len(text), expected["baseline_chars"])

    def vote(self, targets: list[int | None], round_number: int = 1) -> tuple[dict, dict]:
        state = copy.deepcopy(self.fixtures[0]["state"])
        state["phase"] = "VOTE_RUNNING"
        state["pending_ping"] = None
        for seat in state["seats"]:
            seat["eliminated_round"] = None
        state["round"] = round_number
        state["rounds"][0]["round"] = round_number
        undercover.save_state(state)
        args = argparse.Namespace(session=state["session_id"], json=json.dumps({
            str(seat): f"我投{target}号" if target is not None else "我弃权"
            for seat, target in enumerate(targets, 1)
        }))
        with patch.object(undercover, "emit") as emit:
            undercover.cmd_votes_set(args)
        return emit.call_args.args[0], undercover.load_state(state["session_id"])

    def test_fresh_win_instructs_reveal_without_revealing_secrets(self) -> None:
        result, state = self.vote([3, 3, 1, 3, 3, 3])
        self.assertEqual((result["verdict"], result["winner"], state["phase"]), ("finished", "civilian", "FINISHED"))
        self.assertEqual(result["eliminated"]["seat"], 3)
        self.assertIsNone(result["ping"])
        self.assertIn("真相尚未公布", result["next_action"])
        self.assertIn("reveal --session", result["next_action"])
        self.assertIn("uc finish --session", result["next_action"])
        self.assertNotIn("open-round", result["next_action"])
        self.assertNotIn("不要再 reveal", result["next_action"])
        for secret in state["words"].values():
            self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
        # Fresh verdict guidance must not authorize a repeated reveal on ECHO.
        with patch.object(undercover, "emit") as emit:
            undercover.cmd_reveal(argparse.Namespace(session=state["session_id"]))
        self.assertEqual(emit.call_args.args[0]["words"], state["words"])
        with patch.object(undercover, "emit") as emit:
            undercover.cmd_status(argparse.Namespace(session=state["session_id"], full=False))
        self.assertIn("不要再 reveal", emit.call_args.args[0]["next_action"])

    def test_continue_paths_keep_eulogy_human_elimination_and_tie(self) -> None:
        cases = [
            ([4, 1, 1, 1, 1, 1], 1, "eulogy"),
            ([2, 4, 2, 2, 2, 2], 2, "standby"),
            ([None] * 6, None, "standby"),
        ]
        for votes, eliminated, ping_kind in cases:
            with self.subTest(eliminated=eliminated):
                result, state = self.vote(votes)
                self.assertEqual((result["verdict"], state["phase"]), ("continue", "AWAIT_NEXT_ROUND"))
                self.assertEqual(result["eliminated"]["seat"] if result["eliminated"] else None, eliminated)
                self.assertEqual(result["tie"], eliminated is None)
                self.assertEqual(result["ping"]["kind"], ping_kind)
                self.assertEqual(state["pending_ping"], result["ping"])
                if ping_kind == "eulogy":
                    self.assertIn("render-ping", result["next_action"])
                else:
                    self.assertIn("直接 open-round", result["next_action"])
                    self.assertIn("不派预备任务", result["next_action"])
                self.assertNotIn("session complete", result["next_action"])

    def test_round_limit_win_uses_same_terminal_guidance(self) -> None:
        result, state = self.vote([None] * 6, round_number=6)
        self.assertEqual((result["verdict"], result["winner"], state["phase"]), ("finished", "undercover", "FINISHED"))
        self.assertIn("reveal --session", result["next_action"])
        self.assertNotIn("open-round", result["next_action"])

    def test_terminal_prompt_has_explicit_context_privacy_and_completion(self) -> None:
        text, _ = undercover.render_vote_yaml(self.fixtures[0]["state"])
        tally = text.split("      tally:\n", 1)[1]
        for required in ("NODE_TASK/tally，不是 ECHO", "verdict=continue", "verdict=finished",
                         "终局唯一允许公开全员词语和身份", "uc finish --session", "不能使用 bcs_task_complete",
                         "禁止 bcs_route", "命令失败须如实报告", "human 的结构化票面"):
            self.assertIn(required, tally)
        self.assertNotIn("不要输出任何未出局玩家的词语或身份", tally)

    def test_player_dynamic_constraints_keep_round_ladder_and_privacy(self) -> None:
        for rnd, n in ((1, 4), (2, 3), (3, 2), (6, 2)):
            with self.subTest(round=rnd):
                prompt = undercover.bluntness_block(rnd, first=False)
                self.assertIn(f"钝度 {n}", prompt)
                self.assertIn("类目名词", prompt)
                self.assertIn("每轮补充一个", prompt)
                self.assertIn("尚未说过的角度", prompt)
                self.assertIn("信息量以本轮钝度为准", prompt)
                if rnd == 1:
                    self.assertIn("用途和对象不能进同一句", prompt)
                elif rnd == 2:
                    self.assertIn("可加一个使用场合", prompt)
                else:
                    self.assertIn("可以说用途", prompt)
        self.assertIn("首发", undercover.bluntness_block(1, first=True))
        state = self.fixtures[0]["state"]
        speech, _ = undercover.render_speak_yaml(state)
        vote, _ = undercover.render_vote_yaml(state)
        for seat in state["seats"]:
            if seat["kind"] != "bot":
                continue
            for kind, text in (("speak", speech), ("vote", vote)):
                node = text.split(f"      {kind}_{seat['seat']}:\n", 1)[1].split("        transitions:", 1)[0]
                self.assertIn(seat["word"], node)
                other_word = next(word for word in state["words"].values() if word != seat["word"])
                self.assertNotIn(other_word, node)
                if kind == "speak":
                    for constraint in ("25", "拼音", "英文", "谐音", "不提身份"):
                        self.assertIn(constraint, node)
                else:
                    for constraint in ("10", "不能投自己", "我弃权", "全部历轮", "不写理由"):
                        self.assertIn(constraint, node)
                    for constraint in ("相对最可疑", "证据弱也选", "并列者中选一位",
                                       "只有全部历轮都没有可用的玩家描述", "不假定自己一定是平民"):
                        self.assertIn(constraint, node)
                    self.assertNotIn("均无依据则弃权", node)

    def test_successful_submission_starts_players_and_publishes_public_announcement(self) -> None:
        for kind, command in (("speak", undercover.cmd_open_round), ("vote", undercover.cmd_open_vote)):
            for retry in (False, True):
                with self.subTest(kind=kind, retry=retry):
                    state = copy.deepcopy(self.fixtures[0]["state"])
                    state["rounds"][0]["speeches"] = {}
                    state["rounds"][0]["renders"] = {kind: 1} if retry else {}
                    state["phase"] = (
                        "SPEAK_RUNNING" if retry else "AWAIT_NEXT_ROUND"
                    ) if kind == "speak" else "AWAIT_VOTE_START"
                    if kind == "vote" and retry:
                        state["phase"] = "VOTE_RUNNING"
                    undercover.save_state(state)
                    with patch.object(undercover, "require_run_slot"), \
                         patch.object(undercover, "self_lock_hint", return_value=None), \
                         patch.object(undercover, "submit_run", return_value={"run_id": "test-run"}) as submit, \
                         patch.object(undercover, "emit") as emit:
                        command(argparse.Namespace(session=state["session_id"], retry=retry))
                    submit.assert_called_once()
                    result = emit.call_args.args[0]
                    self.assertTrue(result["submitted"])
                    self.assertEqual(result["run_id"], "test-run")
                    yaml_text = Path(submit.call_args.args[1]).read_text()
                    run_input = json.loads(Path(submit.call_args.args[2]).read_text())
                    self.assertNotIn("vote_ready", yaml_text)
                    self.assertNotIn("准备完成", yaml_text)
                    if kind == "speak":
                        self.assertEqual(result["next_action"], undercover.SUBMITTED_NEXT_ACTION)
                        self.assertEqual("作废" in result["announcement"], retry)
                        self.assertEqual(run_input["opening"], result["announcement"])
                        self.assertNotIn("      speak_open:", yaml_text)
                        public_text = result["announcement"]
                    else:
                        self.assertNotIn("announcement", result)
                        self.assertIn("opening", run_input)
                        self.assertIn("立刻结束激活", result["next_action"])
                        panel = json.loads(Path(submit.call_args.args[3]).read_text())
                        public_text = panel["openingAnnouncement"]
                        self.assertEqual(public_text, run_input["opening"])
                        self.assertEqual("之前的票作废" in public_text, retry)
                        self.assertNotIn("vote_open", yaml_text)
                    for secret in state["words"].values():
                        self.assertNotIn(secret, public_text)
                        self.assertNotIn(secret, json.dumps(run_input, ensure_ascii=False))

    def test_graph_starts_without_referee_and_keeps_all_votes_before_tally(self) -> None:
        for fixture in self.fixtures:
            for human_first in (False, True):
                state = copy.deepcopy(fixture["state"])
                if human_first:
                    human = next(seat for seat in state["seats"] if seat["kind"] == "human")
                    human["alive"] = True
                    first = min(state["seats"], key=lambda seat: seat["seat"])
                    first["seat"], human["seat"] = human["seat"], first["seat"]
                living = sorted(undercover.alive_seats(state), key=lambda seat: seat["seat"])
                for kind, render in (("speak", undercover.render_speak_yaml), ("vote", undercover.render_vote_yaml)):
                    text, _ = render(state)
                    blocks = dict(re.findall(r"(?m)^      (\w+):\n([\s\S]*?)(?=^      \w+:\n|\Z)", text))
                    parents = {node: set() for node in blocks}
                    for node, body in blocks.items():
                        targets = re.search(r"targets: \[(.*?)\]", body)
                        for target in targets[1].split(", ") if targets else []:
                            parents[target].add(node)
                    players = [f"{kind}_{seat['seat']}" for seat in living]
                    self.assertEqual({node for node, upstream in parents.items() if not upstream},
                                     {players[0]} if kind == "speak" else {"vote_start"})
                    terminal = "collect" if kind == "speak" else "tally"
                    self.assertEqual(parents[terminal], set(players))
                    for idx, player in enumerate(players):
                        self.assertEqual(parents[player], set(players[:idx]) if kind == "speak" else {"vote_start"})
                        self.assertNotIn("binding: referee", blocks[player])
                    self.assertIn("binding: referee", blocks[terminal])
                    if kind == "vote":
                        self.assertNotIn("binding: referee", blocks["vote_start"])
                        entry = next(seat for seat in living if seat["kind"] == "bot")
                        self.assertIn(f"binding: {entry['binding']}", blocks["vote_start"])
                        self.assertNotIn("vote_start", undercover.public_panel_projection(state, kind, 1)["nodeActorMap"])
                        self.assertNotIn("vote_open", blocks)
                        # Even while the submitting referee is busy, the sole entry
                        # is assigned to a player. No referee callback gates voting.
                        self.assertEqual(set(re.search(r"targets: \[(.*?)\]", blocks["vote_start"])[1].split(", ")), set(players))
                        for secret in state["words"].values():
                            self.assertNotIn(secret, blocks["vote_start"])

    def test_failed_submission_does_not_emit_success_handoff(self) -> None:
        for command, prepare in ((undercover.cmd_open_round, "prepare_speak_run"),
                                 (undercover.cmd_open_vote, "prepare_vote_run")):
            with self.subTest(command=command.__name__):
                payload = dict.fromkeys(("yaml_path", "input_path", "panel_params_path", "panel_tab", "bindings"))
                with patch.object(undercover, "require_run_slot"), \
                     patch.object(undercover, "self_lock_hint", return_value=None), \
                     patch.object(undercover, prepare, return_value=payload), \
                     patch.object(undercover, "submit_run", side_effect=RuntimeError("delivery failed")), \
                     patch.object(undercover, "emit") as emit:
                    with self.assertRaisesRegex(RuntimeError, "delivery failed"):
                        command(argparse.Namespace(session="test-session", retry=False))
                emit.assert_not_called()

    def test_player_common_instructions_are_consistent(self) -> None:
        players = sorted(PROFILES.glob("player-*"))
        self.assertEqual(len(players), 5)
        for filename in ("AGENTS.md", "TOOLS.md", "skills/undercover-game-player/SKILL.md"):
            texts = [(player / filename).read_text() for player in players]
            self.assertEqual(len(set(texts)), 1, filename)
        skill = (players[0] / "skills/undercover-game-player/SKILL.md").read_text()
        self.assertIn("allowed-tools: []", skill)
        for required in ("遗言", "拼音", "谐音", "我弃权", "前序", "钝度"):
            self.assertIn(required, skill)


if __name__ == "__main__":
    unittest.main()
