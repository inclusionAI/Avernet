"""Exercise the player's actual WebSocket entry point with a local BCN peer."""

import asyncio
import json
import unittest
from unittest.mock import patch

from websockets.asyncio.server import serve

from main import Settings, run
from test_player import BOT, SESSION, VOTE, abort_task, send_task


class WebSocketTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sockets = asyncio.Queue()
        self.server = await serve(self.accept, "127.0.0.1", 0)
        self.addAsyncCleanup(self.close_server)

    async def accept(self, socket):
        await self.sockets.put(socket)
        await socket.wait_closed()

    async def close_server(self):
        self.server.close()
        await self.server.wait_closed()

    async def start_player(self, decide):
        port = self.server.sockets[0].getsockname()[1]
        config = Settings(f"ws://127.0.0.1:{port}", BOT, "fixture", "fixture", None, "fixture")
        model = patch("main.AgentsPlayer", return_value=decide)
        model.start()
        self.addCleanup(model.stop)
        player = asyncio.create_task(run(config))
        self.addAsyncCleanup(self.stop_player, player)

    async def stop_player(self, player):
        player.cancel()
        await asyncio.gather(player, return_exceptions=True)

    async def read_frame(self, socket):
        return json.loads(await asyncio.wait_for(socket.recv(), 5))

    async def connect_player(self):
        socket = await asyncio.wait_for(self.sockets.get(), 5)
        hello = await self.read_frame(socket)
        self.assertEqual(hello["method"], "bot.connect")
        self.assertEqual(hello["params"]["protocol_version"], 3)
        self.assertEqual(hello["params"]["token"], "fixture")
        await socket.send(json.dumps({
            "type": "res",
            "id": hello["id"],
            "ok": True,
            "payload": {
                "bot_uuid": BOT,
                "protocol_version": 3,
                "capabilities": {"unified_run_events": True, "canonical_session_id": True},
            },
        }))
        return socket

    async def send_vote(self, socket, run_id, request_id):
        frame = send_task(run_id, VOTE)
        frame["id"] = request_id
        await socket.send(json.dumps(frame))
        self.assertEqual(await self.read_frame(socket), {
            "type": "res",
            "id": request_id,
            "ok": True,
            "payload": {"run_id": run_id},
        })

    async def assert_vote(self, socket, run_id):
        event = await self.read_frame(socket)
        self.assertEqual((event["type"], event["event"]), ("event", "chat"))
        payload = event["payload"]
        self.assertEqual(
            (payload["runId"], payload["sessionId"], payload["state"], payload["content"]),
            (run_id, SESSION, "final", "我投2号"),
        )

    async def test_replayed_run_after_reconnect_does_not_produce_a_vote(self):
        calls = []

        async def decide(task, session, audit):
            calls.append((task.node, session))
            return "我投2号"

        await self.start_player(decide)
        first = await self.connect_player()
        await self.send_vote(first, "original", "send-first")
        await self.assert_vote(first, "original")
        await first.close()

        second = await self.connect_player()
        await self.send_vote(second, "original", "send-replayed")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(second.recv(), 0.2)

        await second.send(json.dumps(abort_task("original")))
        reply = await self.read_frame(second)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["payload"], {"aborted": False, "aborted_run_ids": []})

        await self.send_vote(second, "next", "send-next")
        await self.assert_vote(second, "next")
        self.assertEqual(calls, [("vote_4", SESSION), ("vote_4", SESSION)])

    async def test_structured_task_precedes_display_context(self):
        inputs = []

        async def decide(task, session, audit):
            inputs.append(task.text)
            return "我投2号"

        await self.start_player(decide)
        socket = await self.connect_player()
        display = "〔群上下文〕\n上一轮投票已经结束。"
        frame = send_task("structured", display, session_context={"recipient": BOT, "message": VOTE})
        await socket.send(json.dumps(frame))
        reply = await self.read_frame(socket)
        self.assertTrue(reply["ok"], reply)
        await self.assert_vote(socket, "structured")
        self.assertEqual(inputs, [VOTE])

    async def test_abort_discards_late_vote_on_the_wire(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def decide(task, session, audit):
            if started.is_set():
                return "我投2号"
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            finished.set()
            return "我投1号"

        await self.start_player(decide)
        try:
            socket = await self.connect_player()
            await self.send_vote(socket, "cancelled", "send-slow")
            await asyncio.wait_for(started.wait(), 5)

            await socket.send(json.dumps(abort_task("cancelled", "grp-test:deadbeef")))
            rejected = await self.read_frame(socket)
            self.assertEqual(rejected["error"]["code"], "SCOPE_MISMATCH")
            self.assertFalse(cancelled.is_set())

            await socket.send(json.dumps(abort_task("cancelled")))
            event = await self.read_frame(socket)
            self.assertEqual(event["event"], "chat")
            self.assertEqual(
                (event["payload"]["state"], event["payload"]["runId"], event["payload"]["sessionId"]),
                ("aborted", "cancelled", SESSION),
            )
            response = await self.read_frame(socket)
            self.assertEqual(response["payload"], {"aborted": True, "aborted_run_ids": ["cancelled"]})
            await asyncio.wait_for(cancelled.wait(), 5)

            await self.send_vote(socket, "new", "send-new")
            await self.assert_vote(socket, "new")
            release.set()
            await asyncio.wait_for(finished.wait(), 5)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(socket.recv(), 0.2)
        finally:
            release.set()
