from __future__ import annotations

import unittest

from tests.fixtures import (
    EULOGY_TEXT,
    VOTE_START_INSTRUCTION,
    speech_instruction,
    state_machine_text,
    vote_instruction,
)
from undercover_player.tasks import TaskKind, parse_task, vote_start_text


class ParseTaskTests(unittest.TestCase):
    def test_parses_speech_node(self) -> None:
        text = state_machine_text("speak_3", speech_instruction())
        task = parse_task(text)
        self.assertEqual(task.kind, TaskKind.SPEECH)
        self.assertEqual(task.node_id, "speak_3")
        self.assertEqual(task.seat, 3)
        self.assertEqual(task.word, "苹果")
        self.assertEqual(task.round, 1)
        self.assertEqual(task.max_chars, 25)
        self.assertFalse(task.is_pk)

    def test_parses_pk_speech(self) -> None:
        text = state_machine_text("speak_3", speech_instruction(round_number=2, pk=True))
        task = parse_task(text)
        self.assertEqual(task.kind, TaskKind.SPEECH)
        self.assertTrue(task.is_pk)
        self.assertEqual(task.round, 2)

    def test_parses_vote_with_legal_targets(self) -> None:
        text = state_machine_text("vote_3", vote_instruction())
        task = parse_task(text)
        self.assertEqual(task.kind, TaskKind.VOTE)
        self.assertEqual(task.seat, 3)
        self.assertEqual(task.word, "苹果")
        self.assertEqual(task.legal_targets, (1, 2, 4))
        self.assertTrue(task.allow_abstain)
        self.assertEqual(task.max_chars, 10)

    def test_parses_pk_vote_as_no_abstain(self) -> None:
        text = state_machine_text("vote_3", vote_instruction(round_number=2, pk=True, targets=(1, 2)))
        task = parse_task(text)
        self.assertEqual(task.kind, TaskKind.VOTE)
        self.assertTrue(task.is_pk)
        self.assertFalse(task.allow_abstain)
        self.assertEqual(task.legal_targets, (1, 2))

    def test_parses_vote_start(self) -> None:
        text = state_machine_text("vote_start", VOTE_START_INSTRUCTION)
        task = parse_task(text)
        self.assertEqual(task.kind, TaskKind.VOTE_START)
        self.assertEqual(vote_start_text(), "投票开始，请大家投出一票，等待主持人公布结果。")

    def test_parses_eulogy_and_uses_known_word(self) -> None:
        task = parse_task(EULOGY_TEXT, known_word="苹果", known_seat=3)
        self.assertEqual(task.kind, TaskKind.EULOGY)
        self.assertEqual(task.word, "苹果")
        self.assertEqual(task.seat, 3)
        self.assertEqual(task.max_chars, 35)
        self.assertIn(1, task.candidate_seats)
        self.assertIn(4, task.candidate_seats)

    def test_unknown_text_is_not_guessed(self) -> None:
        task = parse_task("这是一条看不懂的普通消息")
        self.assertEqual(task.kind, TaskKind.UNKNOWN)

    def test_onboarding_text_is_skipped(self) -> None:
        task = parse_task("请使用 bcs-coordination 技能注册到 BCS。执行 bcs-cli onboard --name x")
        self.assertEqual(task.kind, TaskKind.ONBOARDING)


if __name__ == "__main__":
    unittest.main()
