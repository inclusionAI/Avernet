from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from undercover_player import guard

REPO_ROOT = Path(__file__).resolve().parents[3]
REFEREE = (
    REPO_ROOT
    / "singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee/scripts/undercover.py"
)


def _load_referee():
    if not REFEREE.is_file():
        return None
    spec = importlib.util.spec_from_file_location("referee_undercover", REFEREE)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["referee_undercover"] = module
    spec.loader.exec_module(module)
    return module


def _state() -> dict[str, object]:
    return {
        "words": {"civilian": "苹果", "undercover": "香蕉"},
        "config": {"max_rounds": 6},
        "round": 1,
        "seats": [
            {"seat": 1, "word": "苹果", "alive": True, "display": "1号 老陈", "kind": "bot", "bot_uuid": "u1"},
            {"seat": 2, "word": "香蕉", "alive": True, "display": "2号 小满", "kind": "bot", "bot_uuid": "u2"},
            {"seat": 3, "word": "苹果", "alive": True, "display": "3号 阿和", "kind": "bot", "bot_uuid": "u3"},
            {"seat": 4, "word": "苹果", "alive": True, "display": "4号 林工", "kind": "bot", "bot_uuid": "u4"},
        ],
    }


class GuardTests(unittest.TestCase):
    def test_speech_leak_full_word(self) -> None:
        self.assertIsNotNone(guard.speech_violation("苹果很好吃", "苹果", 25))

    def test_speech_leak_split_characters(self) -> None:
        self.assertIsNotNone(guard.speech_violation("苹 果 都 能 吃", "苹果", 25))

    def test_speech_length(self) -> None:
        text = "一二三四五六七八九十一二三四五六七八九十一二三四五六"
        self.assertIsNotNone(guard.speech_violation(text, "苹果", 25))

    def test_speech_accepts_plain_description(self) -> None:
        self.assertIsNone(guard.speech_violation("平时不太动它，占地方。", "苹果", 25))

    def test_speech_rejects_meta_disclosure(self) -> None:
        self.assertIsNotNone(guard.speech_violation("我的词可不能告诉你。", "苹果", 25))

    def test_vote_accepts_legal_target(self) -> None:
        self.assertIsNone(
            guard.vote_violation("我投2号", legal_targets=(1, 2, 4), self_seat=3, allow_abstain=True, max_chars=10)
        )

    def test_vote_rejects_self_and_illegal_target(self) -> None:
        self.assertIsNotNone(
            guard.vote_violation("我投3号", legal_targets=(1, 2, 4), self_seat=3, allow_abstain=True, max_chars=10)
        )
        self.assertIsNotNone(
            guard.vote_violation("我投9号", legal_targets=(1, 2, 4), self_seat=3, allow_abstain=True, max_chars=10)
        )

    def test_vote_abstain_rules(self) -> None:
        self.assertIsNone(
            guard.vote_violation("我弃权", legal_targets=(1, 2, 4), self_seat=3, allow_abstain=True, max_chars=10)
        )
        self.assertIsNotNone(
            guard.vote_violation("我弃权", legal_targets=(1, 2, 4), self_seat=3, allow_abstain=False, max_chars=10)
        )

    def test_eulogy_requires_seat_and_no_leak(self) -> None:
        self.assertIsNone(guard.eulogy_violation("我最不放心2号，那句和场上不合。", "苹果", 35, (1, 2, 4)))
        self.assertIsNotNone(guard.eulogy_violation("我没什么想说的。", "苹果", 35, (1, 2, 4)))
        self.assertIsNotNone(guard.eulogy_violation("我最不放心2号，苹果那句不对。", "苹果", 35, (1, 2, 4)))


class RefereeParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.referee = _load_referee()
        if cls.referee is None:
            raise unittest.SkipTest("referee undercover.py is not available")

    def test_speech_decisions_match_referee(self) -> None:
        state = _state()
        cases = (
            "平时不太动它，占地方。",
            "苹果很好吃",
            "苹 果 都 能 吃",
            "一二三四五六七八九十一二三四五六七八九十一二三四五六",
        )
        for text in cases:
            with self.subTest(text=text):
                _display, reason = self.referee.check_text(state, 1, text, 25)
                ours = guard.speech_violation(text, "苹果", 25)
                self.assertEqual(reason is None, ours is None, f"referee={reason} ours={ours}")

    def test_vote_targets_match_referee(self) -> None:
        state = _state()
        cases = (("我投2号", 2), ("我投4号", 4), ("我弃权", None), ("我投3号", None), ("我投9号", None))
        for text, expected in cases:
            with self.subTest(text=text):
                target, _note = self.referee.parse_ballot(state, 3, text)
                self.assertEqual(target, expected)
                violation = guard.vote_violation(
                    text, legal_targets=(1, 2, 4), self_seat=3, allow_abstain=True, max_chars=10
                )
                if "弃权" in text or expected is not None:
                    self.assertIsNone(violation)
                else:
                    self.assertIsNotNone(violation)
                if expected is not None:
                    self.assertEqual(guard.vote_target(text), expected)


if __name__ == "__main__":
    unittest.main()
