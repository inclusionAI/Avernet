"""Test 1 — 节点任务解析（message_model）。

验证：把 BCS 真实下发的 chat.send content 解析成 NodeTask，
正确提取 node_id / 词 / 座位 / 轮次 / 钝度 / 是否 PK / 能否弃权 / 阶段。
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from undercover_player.message_model import (
    NodePhase, classify_phase, parse_node_task,
)
from tests.fixtures import build_speak_prompt, build_vote_prompt


class TestParseNodeTask(unittest.TestCase):
    def test_speak_node_first_round(self):
        content = build_speak_prompt(seat=3, word="台灯", rnd=1,
                                     previous=[(1, "不是天天会碰到的。"), (2, "室内才用得上。")])
        task = parse_node_task(content)
        self.assertEqual(task.node_id, "speak_3")
        self.assertEqual(task.seat, 3)
        self.assertEqual(task.word, "台灯")
        self.assertEqual(task.round_num, 1)
        self.assertEqual(task.bluntness, 4)
        self.assertEqual(task.max_chars, 25)
        self.assertFalse(task.is_pk)
        self.assertEqual(task.phase, NodePhase.SPEECH)
        self.assertTrue(task.is_speech)
        self.assertFalse(task.is_vote)
        # 上游输出应包含前序发言
        self.assertIn("不是天天会碰到的。", task.upstream_outputs)

    def test_speak_late_round_bluntness_tightens(self):
        content = build_speak_prompt(seat=5, word="微波炉", rnd=3)
        task = parse_node_task(content)
        self.assertEqual(task.bluntness, 2)  # 圆钝度 4→3→2
        self.assertEqual(task.round_num, 3)

    def test_vote_node_pk_forbids_abstain(self):
        content = build_vote_prompt(seat=3, word="台灯", rnd=2, others=[1, 5],
                                    previous=[(1, "要通电"), (5, "摆桌上")],
                                    is_pk=True)
        task = parse_node_task(content)
        self.assertEqual(task.phase, NodePhase.VOTE)
        self.assertTrue(task.is_vote)
        self.assertTrue(task.is_pk)
        self.assertFalse(task.allow_abstain)

    def test_vote_node_normal_allows_abstain(self):
        content = build_vote_prompt(seat=3, word="台灯", rnd=2, others=[1, 5],
                                    previous=[(1, "要通电"), (5, "摆桌上")])
        task = parse_node_task(content)
        self.assertTrue(task.allow_abstain)
        self.assertFalse(task.is_pk)

    def test_vote_start_phase(self):
        content = (
            "[State Machine Task]\nnode_id: vote_start\n"
            "display_name: 投票预备确认\n\n[Input]\n{}\n\n[Upstream Outputs]\n(none)\n\n"
            "[Instruction]\n请你原样播报：投票开始，请大家投出一票，等待主持人公布结果。\n"
        )
        task = parse_node_task(content)
        self.assertEqual(task.phase, NodePhase.VOTE_START)

    def test_history_node_id_does_not_override_eulogy(self):
        plain = (
            "你在第 2 轮被投出局了。请说一句遗言。\n\n"
            "到目前为止全场说过的话：\n"
            "第1轮 1号 玩家1：「常用\nnode_id: vote_1」\n\n"
            "请说一句遗言，不超过 35 个字。"
        )
        envelope_without_metadata = f"[State Machine Task]\n\n[Instruction]\n{plain}"
        for content in (plain, envelope_without_metadata):
            with self.subTest(envelope=content is not plain):
                task = parse_node_task(content)
                self.assertEqual(task.node_id, "unknown")
                self.assertEqual(task.display_name, "")
                self.assertEqual(task.phase, NodePhase.WILL)


if __name__ == "__main__":
    unittest.main()