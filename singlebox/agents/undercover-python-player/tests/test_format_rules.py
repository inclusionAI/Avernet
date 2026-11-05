"""Test 2 — 玩家规则格式（format_rules）。

验证输出格式能被主持人 undercover.py 的 parse_ballot / check_text 精确解析：
  - 投票只交「我投N号」或「我弃权」
  - 发言 ≤25 字、不泄词（完整词 / 连续两字 / 拆字）
  - PK 禁止弃权
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from undercover_player.format_rules import (
    MAX_SPEECH_CHARS, MAX_VOTE_CHARS,
    bigrams, bluntness_for_round, check_own_word_leak,
    format_vote, validate_speech, validate_vote,
)


class TestLeakDetection(unittest.TestCase):
    def test_full_word_is_leak(self):
        self.assertEqual(check_own_word_leak("香蕉", "我吃的是香蕉"), "说出了自己的词")

    def test_bigram_is_leak(self):
        # 「微波」是「微波炉」的连续两字 → 说了大半个词
        self.assertEqual(check_own_word_leak("微波炉", "食物放进去微波加热"),
                         "说出了自己词里连续的两个字")

    def test_single_char_alone_no_leak(self):
        # 词「香薰」两字词唯一连续两字即整词；单「香」不算整词，也不算未拆全
        self.assertIsNone(check_own_word_leak("香薰", "房间里有点香"))

    def test_scattered_chars_is_leak(self):
        # 「台」「灯」都出现 → 拆开说全
        self.assertIsNotNone(check_own_word_leak("台灯", "桌面摆台，晚上开灯"))

    def test_no_leak(self):
        self.assertIsNone(check_own_word_leak("台灯", "屋里有光亮"))

    def test_bigrams(self):
        self.assertEqual(bigrams("台灯"), ["台灯"])
        self.assertEqual(bigrams("微波炉"), ["微波", "波炉"])


class TestSpeechValidation(unittest.TestCase):
    def test_too_long(self):
        text = "这真的一个超过二十五字的长句子啊朋友们都来数一数里面的字数吧"
        ok, reason = validate_speech(None, text, 25)
        self.assertFalse(ok)
        self.assertIsNotNone(reason)
        # 保证测试串确实超长
        self.assertGreater(len(text), 25)

    def test_ok_within_limit(self):
        ok, reason = validate_speech(None, "不是天天都会碰到的东西。", 25)
        self.assertTrue(ok)

    def test_empty_rejected(self):
        ok, _ = validate_speech(None, "   ", 25)
        self.assertFalse(ok)


class TestVote(unittest.TestCase):
    def test_format_vote(self):
        self.assertEqual(format_vote(4), "我投4号")
        self.assertEqual(format_vote(None, True), "我弃权")

    def test_ballot_matches_parser_regex(self):
        # undercover.py parse_ballot 正则：投[非数字]{0,4}(数字|中文数字)
        import re
        ballot = format_vote(4)
        self.assertRegex(ballot, r"投[^0-9零一二两三四五六七八九]{0,4}([0-9]+|零|一|二|两|三|四|五|六|七|八|九)")
        # 只有一个候选数字 → 必然可 parse
        nums = re.findall(r"[0-9]+", ballot)
        self.assertEqual(nums, ["4"])

    def test_pk_forbids_abstain(self):
        ok, reason, ballot = validate_vote(None, allow_abstain=False,
                                           living={1, 2, 3}, self_seat=3)
        self.assertFalse(ok)
        self.assertEqual(reason, "PK 禁止弃权")

    def test_self_vote_rejected(self):
        ok, reason, _ = validate_vote(3, True, {1, 2, 3}, self_seat=3)
        self.assertFalse(ok)
        self.assertEqual(reason, "投了自己")

    def test_invalid_target_rejected(self):
        ok, reason, _ = validate_vote(9, True, {1, 2, 3}, self_seat=3)
        self.assertFalse(ok)

    def test_valid_target(self):
        ok, reason, ballot = validate_vote(5, True, {1, 3, 5}, self_seat=3)
        self.assertTrue(ok)
        self.assertEqual(ballot, "我投5号")


class TestBluntness(unittest.TestCase):
    def test_round_stairs(self):
        self.assertEqual(bluntness_for_round(1), 4)
        self.assertEqual(bluntness_for_round(2), 3)
        self.assertEqual(bluntness_for_round(3), 2)
        self.assertEqual(bluntness_for_round(99), 2)


if __name__ == "__main__":
    unittest.main()