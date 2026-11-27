import asyncio
from collections import OrderedDict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bcn import BotConnection, request
from player import InvalidOutput, parse_task, validate_output
from prepare_profile import prepare

SESSION = "grp-test:a1b2c3d4"
BOT = "xiaoman"
SPEECH = (
    "[from:BCS State Machine][State Machine Task]\nnode_id: speak_4\ndisplay_name: 4号发言\n"
    "\n[Input]\n{\"round\": 1}\n\n[Upstream Outputs]\n[speak_1]\n常见在桌边。\n"
    "\n[Instruction]\n你是 4 号，你的词是【手电筒】。第 1 轮发言。\n"
    "输出一句话，不超过 25 字，描述你的词。\n"
)
VOTE = (
    "[State Machine Task]\nnode_id: vote_4\ndisplay_name: 4号投票\n"
    "\n[Input]\n{\"round\": 1}\n\n[Instruction]\n"
    "第 1 轮投票。只输出「我投N号」或「我弃权」。\n"
    "你是 4 号，词是【手电筒】。全场：1号、2号、4号。\n可以投：1号、2号；不能投自己。"
)


class Socket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return await self.incoming.get()


def send_task(run_id, text, **overrides):
    return request("chat.send", {
        "session_key": SESSION,
        "bcs_session_id": SESSION,
        "idempotency_key": run_id,
        "session_context": {"recipient": BOT},
        "message": {"content": [{"type": "text", "text": text}]},
        **overrides,
    }, "send-" + run_id)


def abort_task(run_id, session=SESSION):
    return request("chat.abort", {"session_key": session, "run_id": run_id}, "abort-" + run_id)


def terminals(socket):
    return [frame["payload"] for frame in socket.sent if frame.get("event") == "chat"]


class RulesTest(unittest.TestCase):
    def test_speech_respects_length_and_secret(self):
        task = parse_task(SPEECH)
        self.assertEqual(task.node, "speak_4")
        self.assertEqual(validate_output(task, "停电时常有人想起它。", task.word), "停电时常有人想起它。")
        for output in ("电筒很好用", "手里握着，没电就是一截圆筒", "太" * 26, "第一句。又一句"):
            with self.subTest(output=output), self.assertRaises(InvalidOutput):
                validate_output(task, output, task.word)

    def test_vote_only_returns_canonical_legal_choices(self):
        task = parse_task(VOTE)
        self.assertEqual(validate_output(task, "我投 2 号", task.word), "我投2号")
        self.assertEqual(validate_output(task, "我弃权", task.word), "我弃权")
        pk = parse_task(VOTE.replace("第 1 轮投票", "第 1 轮 PK 投票"))
        for candidate, output in ((task, "我投4号"), (task, "我投2号，因为很可疑"), (pk, "我弃权")):
            with self.subTest(output=output), self.assertRaises(InvalidOutput):
                validate_output(candidate, output, task.word)


class ProfileTest(unittest.TestCase):
    def test_replacement_keeps_the_other_gateway_ports(self):
        original = Path(__file__).resolve().parent.parent
        source = json.loads((original / "bots.json").read_text())

        def ports(manifest):
            return {
                bot["source"]: manifest["port_start"] + index * manifest["port_step"]
                for index, bot in enumerate(manifest["bots"])
            }

        expected = ports(source)
        del expected["player-xiaoman"]
        with TemporaryDirectory() as directory:
            output = Path(directory)
            prepare(output)
            prepare(output)
            actual = {}
            for path in output.glob("*/bots.json"):
                roster = json.loads(path.read_text())
                actual.update(ports(roster))
                for bot in roster["bots"]:
                    self.assertEqual((path.parent / bot["source"]).resolve(), original / bot["source"])
            self.assertEqual(actual, expected)


class RunTest(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_abort_and_late_result(self):
        entered, cancelled, release, late = (asyncio.Event() for _ in range(4))

        async def decide(task, session, audit):
            self.assertEqual(session, SESSION)
            if task.kind == "vote":
                return "我投2号"
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                late.set()
                return "这个迟到的结果不能回传。"

        socket = Socket()
        bot = BotConnection(socket, BOT, decide, OrderedDict())
        await bot.handle(send_task("old", SPEECH))
        self.assertEqual(socket.sent[0]["payload"], {"run_id": "old"})
        await asyncio.wait_for(entered.wait(), 2)
        await bot.handle(abort_task("old", "grp-test:deadbeef"))
        self.assertEqual(socket.sent[-1]["error"]["code"], "SCOPE_MISMATCH")
        await bot.handle(abort_task("old"))
        self.assertEqual(socket.sent[-1]["payload"], {"aborted": True, "aborted_run_ids": ["old"]})
        await asyncio.wait_for(cancelled.wait(), 2)

        await bot.handle(send_task("new", VOTE))
        await asyncio.wait_for(bot.pending["new"].job, 2)
        release.set()
        await asyncio.wait_for(late.wait(), 2)
        await asyncio.sleep(0)
        await bot.handle(send_task("old", SPEECH))
        await bot.handle(abort_task("old"))
        self.assertEqual(socket.sent[-1]["payload"], {"aborted": False, "aborted_run_ids": []})
        events = terminals(socket)
        self.assertEqual([(p["runId"], p["state"], p["sessionId"], p["seq"]) for p in events],
                         [("old", "aborted", SESSION, 1), ("new", "final", SESSION, 1)])
        self.assertNotIn("content", events[0])
        self.assertEqual(events[1]["content"], "我投2号")

    async def test_timeout_claims_terminal_before_cancelling_model(self):
        cancelled = asyncio.Event()

        async def decide(*args):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                return "过期了才返回。"

        socket = Socket()
        bot = BotConnection(socket, BOT, decide, OrderedDict())
        await bot.handle(send_task("deadline", SPEECH, timeout_ms=40))
        await asyncio.wait_for(bot.pending["deadline"].job, 2)
        await asyncio.wait_for(cancelled.wait(), 2)
        self.assertEqual([(p["state"], p.get("errorCode")) for p in terminals(socket)],
                         [("error", "TIMEOUT")])

    async def test_inject_and_foreign_traffic_never_call_model(self):
        async def decide(*args):
            self.fail("no model call was expected")

        socket = Socket()
        bot = BotConnection(socket, BOT, decide, OrderedDict())
        await bot.handle(request("chat.inject", {
            "session_key": SESSION, "bcs_session_id": SESSION,
            "message": {"content": [{"type": "text", "text": SPEECH}]},
        }, "inject"))
        await bot.handle(send_task("foreign", SPEECH, session_context={"recipient": "another-player"}))
        await bot.handle(send_task("group-only", SPEECH, session_key="grp-test"))
        self.assertEqual([frame["ok"] for frame in socket.sent], [True, False, False])
        self.assertFalse(terminals(socket))
        self.assertFalse(bot.pending)


if __name__ == "__main__":
    unittest.main()
