import asyncio
import json
import unittest

from player import DecisionError, Player


def vote_task(instruction: str, history: list | None = None) -> str:
    if history is None:
        history = [{"round": 1, "speeches": [{"seat": 2, "text": "这是公开发言"}]}]
    public = json.dumps({"history": history}, ensure_ascii=False)
    return (
        "[State Machine Task]\nnode_id: vote_1\ndisplay_name: 1号投票\n\n"
        f"[Input]\n{public}\n\n[Upstream Outputs]\n预备词\n\n"
        f"[Instruction]\n{instruction}"
    )


class PlayerTest(unittest.IsolatedAsyncioTestCase):
    async def test_vote_uses_only_public_history_and_eligible_seats(self) -> None:
        received = []

        async def generate(prompt: str) -> str:
            received.append(prompt)
            return "我投2号"

        task = vote_task(
            "第 1 轮投票。你是 1 号，词是【秘密】。\n可以投：2号、3号；不能投自己。"
        )
        self.assertEqual(await Player(generate).reply(task), "我投2号")
        self.assertIn("公开发言", received[0])
        self.assertNotIn("预备词", received[0])
        self.assertNotIn("秘密", received[0])

    async def test_pk_retries_an_illegal_vote_and_returns_eligible_seat(self) -> None:
        responses = iter(("我弃权", "我投4号"))

        async def generate(prompt: str) -> str:
            await asyncio.sleep(0)
            return next(responses)

        task = vote_task("第 2 轮PK 投票。你是 1 号。\n可以投：2号、4号；不能投自己。")
        self.assertEqual(await Player(generate).reply(task), "我投4号")

    async def test_regular_vote_without_public_speech_explicitly_abstains(self) -> None:
        async def generate(prompt: str) -> str:
            self.fail("an empty public history should not call the model")

        task = vote_task("第 1 轮投票。你是 1 号。\n可以投：2号、3号；不能投自己。", [])
        self.assertEqual(await Player(generate).reply(task), "我弃权")

    async def test_speech_rejects_leaks_and_over_length_output(self) -> None:
        responses = iter(("我想到苹果", "字" * 26))

        async def generate(prompt: str) -> str:
            return next(responses)

        task = (
            "[State Machine Task]\nnode_id: speak_1\ndisplay_name: 1号发言\n\n"
            "[Input]\n公开信息\n\n[Upstream Outputs]\n前序发言\n\n"
            "[Instruction]\n你是 1 号，你的词是【苹果】。第 1 轮发言。"
        )
        with self.assertRaises(DecisionError):
            await Player(generate).reply(task)
