"""Test 4 — 玩家决策器（agent）。

验证：
  - 发言 ≤25 字、不泄词、按钝度给宽泛描述
  - 投票只交「我投N号」/「我弃权」，PK 禁止弃权
  - 投票预备原样播报
"""
import asyncio
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from undercover_player.agent import UndercoverPlayer
from undercover_player.engine import ChatRunContext
from undercover_player.format_rules import check_own_word_leak
from undercover_player.message_model import NodeTask
from tests.fixtures import build_speak_prompt, build_vote_prompt, build_vote_prompt_from_history


def _task_from(content) -> NodeTask:
    from undercover_player.message_model import parse_node_task
    return parse_node_task(content)


def _ctx(task):
    return ChatRunContext(run_id="run-1", session_key="sess-1", task=task)


class TestSpeech(unittest.TestCase):
    def test_speech_within_limit_and_no_leak(self):
        player = UndercoverPlayer()
        task = _task_from(build_speak_prompt(3, "台灯", 1))
        text = player.make_speech(task)
        self.assertLessEqual(len(text), task.max_chars)
        self.assertIsNone(check_own_word_leak("台灯", text))

    def test_speech_across_rounds_obey_bluntness(self):
        player = UndercoverPlayer()
        r1 = _task_from(build_speak_prompt(3, "台灯", 1))
        r3 = _task_from(build_speak_prompt(3, "台灯", 3))
        # 钝度越大越抽象——此处仅验证都能产出合法句
        self.assertIsNone(check_own_word_leak(r1.word, player.make_speech(r1)))
        self.assertIsNone(check_own_word_leak(r3.word, player.make_speech(r3)))


class TestVote(unittest.TestCase):
    def test_vote_only_ballot_format(self):
        player = UndercoverPlayer()
        task = _task_from(build_vote_prompt(
            3, "台灯", 2, others=[1, 5],
            previous=[(1, "要通电"), (5, "摆桌上")]))
        ballot = asyncio.run(player.make_vote(task))
        self.assertIn(ballot, ("我投1号", "我投5号", "我弃权"))

    def test_pk_cannot_abstain(self):
        player = UndercoverPlayer()
        task = _task_from(build_vote_prompt(
            3, "台灯", 2, others=[1, 5],
            previous=[(1, "要通电"), (5, "摆桌上")], is_pk=True))
        self.assertTrue(task.is_pk)
        self.assertFalse(task.allow_abstain)
        ballot = asyncio.run(player.make_vote(task))
        # PK 必须给票号，不能是「我弃权」
        self.assertIsNotNone(ballot)
        self.assertTrue(ballot.startswith("我投"),
                        f"PK 票面应为票号，实际: {ballot!r}")

    def test_abstain_when_no_candidates(self):
        player = UndercoverPlayer()
        task = _task_from(build_vote_prompt(3, "台灯", 2, others=[1, 5],
                                            previous=[]))
        ballot = asyncio.run(player.make_vote(task))
        self.assertEqual(ballot, "我弃权")


class TestVoteFromRefereeJson(unittest.TestCase):
    def _history(self):
        # undercover.py::public_history() 的真实结构
        return [
            {"round": 1, "speeches": [
                {"seat": 1, "player": "1号 阿和", "text": "要通电"},
                {"seat": 2, "player": "2号 老陈", "text": "晚上才用"},
                {"seat": 5, "player": "5号 小满", "text": "摆桌上"},
            ]},
            {"round": 2, "speeches": [
                {"seat": 1, "player": "1号 阿和", "text": "屋里得有光"},
                {"seat": 5, "player": "5号 小满", "text": "随手就能开"},
            ]},
        ]

    def test_decodes_json_speeches_not_strings(self):
        player = UndercoverPlayer()
        task = _task_from(build_vote_prompt_from_history(
            3, "台灯", 2, alive=[1, 2, 5], others=[1, 5], history=self._history()))
        self.assertEqual(
            player._speaks(task),
            [(1, "要通电"), (2, "晚上才用"), (5, "摆桌上"),
             (1, "屋里得有光"), (5, "随手就能开")],
        )
        ballot = asyncio.run(player.make_vote(task))
        self.assertIn(ballot, ("我投1号", "我投5号"))

    def test_pk_with_json_history_never_abstains(self):
        player = UndercoverPlayer()
        task = _task_from(build_vote_prompt_from_history(
            3, "台灯", 2, alive=[1, 2, 5], others=[1, 5],
            history=self._history(), is_pk=True))
        self.assertFalse(task.allow_abstain)
        ballot = asyncio.run(player.make_vote(task))
        self.assertIsNotNone(ballot)
        self.assertTrue(ballot.startswith("我投"), ballot)

    def test_eliminated_speaker_is_not_a_candidate(self):
        player = UndercoverPlayer()
        # 2 号已出局，但历史里仍有其发言；合法目标只有 1/5
        task = _task_from(build_vote_prompt_from_history(
            3, "台灯", 2, alive=[1, 3, 5], others=[1, 5], history=self._history()))
        self.assertEqual(task.alive_seats, (1, 3, 5))
        self.assertEqual(task.allowed_targets, (1, 5))
        self.assertEqual(player._candidates(task), [1, 5])


class TestVoteStart(unittest.TestCase):
    def test_broadcasts_opening(self):
        player = UndercoverPlayer()
        content = (
            "[State Machine Task]\nnode_id: vote_start\n"
            "display_name: 投票预备确认\n\n[Input]\n{}\n\n[Upstream Outputs]\n(none)\n\n"
            "[Instruction]\n请你原样播报：投票开始，请大家投出一票，等待主持人公布结果。\n"
        )
        task = _task_from(content)
        ballot = asyncio.run(player.decide(_ctx(task), task))
        self.assertEqual(ballot, "投票开始，请大家投出一票，等待主持人公布结果。")


if __name__ == "__main__":
    unittest.main()