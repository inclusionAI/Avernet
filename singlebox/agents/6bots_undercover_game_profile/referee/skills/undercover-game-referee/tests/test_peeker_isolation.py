"""
偷看者(Peeker)隔离测试:多人类对局下,任何一位玩家都不能通过 profile 生成的
渲染产物 / 公开 panel 参数 / 其他玩家 Bot 节点指令 看到别人的词。

范围定义:
- 本测试断言 profile 层(undercover.py 渲染)与 panel 层(公开参数)的隔离边界。
- "以人类 A 身份调所有接口/副屏,断言拿不到 B 的词" —— 在 profile/panel 范围内,
  等价于断言:任何单座位的 HumanInput 指令、任何公开 panel 参数、任何 Bot 节点
  指令都不携带其他玩家的私有词。
- BCS 服务端"未指定 assignee 的 frontend human_input 对所有 Present 人类可见"的
  泄露点属于服务端职责,已写入同目录 RFC(<RFC-101>、threat-model-multiplayer.md),
  不在 profile/panel 测试范围内强制实现。

fixtures 采用多人类(2 Human + 3 Bot)对局,直接构造 state,不依赖 bcs-cli。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_undercover_panel import SCRIPT, undercover

BOTH_WORDS = ("隐词甲", "隐词乙")  # civilian / undercover


def build_multi_human_state() -> dict:
    """2 Human + 3 Bot,总 5 人,1 卧底的干净 AWAIT_START 状态。"""
    state = {
        "session_id": "group-game:multi-h1",
        "group_id": "group-game",
        "human_actor_id": "human-A",
        "human_actor_ids": ["human-A", "human-B"],
        "referee_uuid": "referee-1",
        "phase": "AWAIT_START",
        "config": {"max_rounds": 6, "undercover": 1, "players": 5},
        "words": {"civilian": "隐词甲", "undercover": "隐词乙"},
        "round": 1,
        "rounds": [
            {
                "round": 1,
                "order": [1, 2, 3, 4, 5],
                "speeches": {}, "votes": {}, "counts": {}, "renders": {},
            }
        ],
        "pending_ping": None,
        "result": None,
        "seats": [
            {"seat": 1, "kind": "human", "display": "你", "bot_name": "你", "bot_uuid": None, "binding": None,
             "actor_id": "human-A", "word": "隐词甲", "role": "civilian", "alive": True, "eliminated_round": None},
            {"seat": 2, "kind": "human", "display": "你", "bot_name": "你", "bot_uuid": None, "binding": None,
             "actor_id": "human-B", "word": "隐词甲", "role": "civilian", "alive": True, "eliminated_round": None},
            {"seat": 3, "kind": "bot", "display": "悟空", "bot_name": "玩家悟空", "bot_uuid": "bot-wukong", "binding": "seat3",
             "word": "隐词甲", "role": "civilian", "alive": True, "eliminated_round": None},
            {"seat": 4, "kind": "bot", "display": "八戒", "bot_name": "玩家八戒", "bot_uuid": "bot-bajie", "binding": "seat4",
             "word": "隐词乙", "role": "undercover", "alive": True, "eliminated_round": None},
            {"seat": 5, "kind": "bot", "display": "沙僧", "bot_name": "玩家沙僧", "bot_uuid": "bot-shaseng", "binding": "seat5",
             "word": "隐词甲", "role": "civilian", "alive": True, "eliminated_round": None},
        ],
    }
    return state


class PeekerIsolationTest(unittest.TestCase):
    """以"偷看者"视角验证多人类对局的上下文隔离。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {"BOT_DATA_DIR": cls.temp_dir.name})
        cls.env.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.env.stop()
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.state = build_multi_human_state()

    # ---- 发言 HumanInput:任一人类的词只出现在他自己的指令里 ----

    def test_speech_human_A_instruction_does_not_leak_human_B_word(self) -> None:
        text, _ = undercover.render_speak_yaml(self.state)
        node_a, node_b = self._speak_node(text, 1), self._speak_node(text, 2)
        # A 只看到自己的词(隐词甲)
        self.assertIn("隐词甲", node_a)
        # A 看不见 B 的座位/词;two human civilians share 隐词甲,用座位号区分私有性
        self.assertNotIn("你是 2 号", node_a)
        # B 的指令里没有 A 私有信息(B 与 A 同词,但座位不同)
        self.assertNotIn("你是 1 号", node_b)

    def test_speech_each_human_node_has_only_own_seat_number(self) -> None:
        text, _ = undercover.render_speak_yaml(self.state)
        for seat in (1, 2):
            node = self._speak_node(text, seat)
            self.assertIn(f"你是 {seat} 号", node)
            other = 2 if seat == 1 else 1
            self.assertNotIn(f"你是 {other} 号", node)

    def test_public_speech_panel_params_contain_no_word(self) -> None:
        panel = undercover.public_panel_projection(self.state, "speak", 1)
        blob = json.dumps(panel, ensure_ascii=False)
        for word in BOTH_WORDS:
            self.assertNotIn(word, blob)
        self.assertNotIn("word", blob)

    # ---- 投票 HumanInput ----

    def test_vote_human_A_instruction_does_not_leak_other_human_vote(self) -> None:
        text, _ = undercover.render_vote_yaml(self.state)
        node_a = self._vote_node(text, 1)
        node_b = self._vote_node(text, 2)
        self.assertIn("你是 1 号", node_a)
        self.assertNotIn("你是 2 号", node_a)
        self.assertIn("你是 2 号", node_b)
        self.assertNotIn("你是 1 号", node_b)

    def test_public_vote_panel_params_contain_no_word(self) -> None:
        panel = undercover.public_panel_projection(self.state, "vote", 1)
        blob = json.dumps(panel, ensure_ascii=False)
        for word in BOTH_WORDS:
            self.assertNotIn(word, blob)

    # ---- Bot 节点指令不携带别的玩家的词 ----

    def test_bot_nodes_do_not_contain_any_human_word_as_secret(self) -> None:
        text, _ = undercover.render_speak_yaml(self.state)
        for seat in (3, 4, 5):
            node = self._speak_node(text, seat)
            # Bot 节点只知道自己的词;别的玩家的词(另一词)不得出现
            own = next(seat_item["word"] for seat_item in self.state["seats"] if seat_item["seat"] == seat)
            other_word = next(word for word in BOTH_WORDS if word != own)
            self.assertNotIn(other_word, node)

    def test_bot_instruction_isolates_word_per_binding(self) -> None:
        text, _ = undercover.render_vote_yaml(self.state)
        for seat in (3, 4, 5):
            own = next(seat_item["word"] for seat_item in self.state["seats"] if seat_item["seat"] == seat)
            node = self._vote_node(text, seat)
            self.assertIn(own, node)
            for other in BOTH_WORDS:
                if other != own:
                    self.assertNotIn(other, node)

    # ---- 副屏/接口视角:以人类 A 身份遍历所有输出 ----

    def test_viewer_A_perspective_full_output_has_only_A_word(self) -> None:
        """以 A 为 currentViewer,整段 YAML 里 A 座位以外的私有词只出现在对应 Bot 节点,
        不进入 A 可读的公开 panel 参数。"""
        for kind in ("speak", "vote"):
            panel = undercover.public_panel_projection(self.state, kind, 1)
            json_payload = json.dumps(panel, ensure_ascii=False)
            for word in BOTH_WORDS:
                self.assertNotIn(word, json_payload, f"{kind} panel leaked {word}")

    # ---- 辅助 ----

    def _speak_node(self, yaml_text: str, seat: int) -> str:
        return self._node_block(yaml_text, f"speak_{seat}")

    def _vote_node(self, yaml_text: str, seat: int) -> str:
        return self._node_block(yaml_text, f"vote_{seat}")

    @staticmethod
    def _node_block(yaml_text: str, node_id: str) -> str:
        marker = f"      {node_id}:\n"
        idx = yaml_text.find(marker)
        if idx < 0:
            return ""
        body = yaml_text[idx + len(marker):]
        # 下一个同级节点或文件结束为止
        nxt = re.search(r"\n      \w+:", body)
        return body[: nxt.start()] if nxt else body


if __name__ == "__main__":
    unittest.main()