import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.server import serve as websocket_server
from websockets.exceptions import WebSocketException

from bcn import now_ms
from player import Player
from run_player import Identity, StartupError, serve


class TransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_negotiation_ack_terminal_and_private_identity(self) -> None:
        received = asyncio.get_running_loop().create_future()
        completion = asyncio.Event()

        async def fake_bcn(socket) -> None:
            try:
                registration = json.loads(await socket.recv())
                self.assertEqual(registration["method"], "bot.connect")
                self.assertEqual(registration["params"]["protocol_version"], 3)
                await socket.send(
                    json.dumps(
                        {
                            "type": "res",
                            "id": registration["id"],
                            "ok": True,
                            "payload": {
                                "token": "test-only",
                                "bot_uuid": "test-bot",
                                "protocol_version": 3,
                                "capabilities": {
                                    "unified_run_events": True,
                                    "canonical_session_id": True,
                                },
                            },
                        }
                    )
                )
                session = "game:1234abcd"
                await socket.send(
                    json.dumps(
                        {
                            "type": "req",
                            "id": "task",
                            "method": "chat.send",
                            "params": {
                                "bcs_session_id": session,
                                "session_key": session,
                                "timeout_ms": 5000,
                                "message": {
                                    "timestamp": now_ms(),
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "收到请只回一句话，表示你准备好了",
                                        }
                                    ],
                                },
                            },
                        }
                    )
                )
                ack = json.loads(await socket.recv())
                terminal = json.loads(await socket.recv())
                received.set_result((ack, terminal))
            except (AssertionError, KeyError, ValueError, WebSocketException) as error:
                received.set_exception(error)
            await completion.wait()

        async def unexpected_model_call(prompt: str) -> str:
            self.fail("standby must not call Qwen")

        with tempfile.TemporaryDirectory() as temporary:
            identity_path = Path(temporary) / ".bcs" / "session.json"
            async with websocket_server(fake_bcn, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                task = asyncio.create_task(
                    serve(
                        f"ws://127.0.0.1:{port}",
                        identity_path,
                        Player(unexpected_model_call),
                    )
                )
                try:
                    ack, terminal = await asyncio.wait_for(received, 5)
                    self.assertEqual(ack["id"], "task")
                    self.assertEqual(
                        terminal["payload"]["runId"], ack["payload"]["run_id"]
                    )
                    self.assertEqual(terminal["payload"]["content"], "我准备好了。")
                    self.assertEqual(
                        Identity.load(identity_path), Identity("test-only", "test-bot")
                    )
                finally:
                    completion.set()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_unwritable_identity_stops_registration_retries(self) -> None:
        async def fake_bcn(socket) -> None:
            registration = json.loads(await socket.recv())
            await socket.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": registration["id"],
                        "ok": True,
                        "payload": {
                            "token": "test-only",
                            "bot_uuid": "test-bot",
                            "protocol_version": 3,
                            "capabilities": {
                                "unified_run_events": True,
                                "canonical_session_id": True,
                            },
                        },
                    }
                )
            )

        async def unexpected_model_call(prompt: str) -> str:
            self.fail("startup must not call Qwen")

        with tempfile.TemporaryDirectory() as temporary:
            blocker = Path(temporary) / "not_a_directory"
            blocker.touch()
            async with websocket_server(fake_bcn, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with self.assertRaises(StartupError):
                    await asyncio.wait_for(
                        serve(
                            f"ws://127.0.0.1:{port}",
                            blocker / "session.json",
                            Player(unexpected_model_call),
                        ),
                        1,
                    )
