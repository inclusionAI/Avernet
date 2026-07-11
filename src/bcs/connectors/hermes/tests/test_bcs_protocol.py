from __future__ import annotations

import asyncio
import ast
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.server import serve


CONNECTOR_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONNECTOR_DIR))

from hermes_bcn import AtomicJsonStore, BcsClient, _open_websocket  # noqa: E402


class BcsProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    async def test_atomic_store_replaces_with_owner_only_mode(self) -> None:
        path = Path(self.tempdir.name) / "nested" / "state.json"
        store = AtomicJsonStore(path)

        store.save({"bot_token": "secret", "count": 1})
        store.save({"bot_token": "rotated", "count": 2})

        self.assertEqual(
            {"bot_token": "rotated", "count": 2},
            json.loads(path.read_text(encoding="utf-8")),
        )
        self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
        self.assertEqual([], list(path.parent.glob(f".{path.name}.*.tmp")))

    async def test_connector_source_parses_as_python_311(self) -> None:
        source = (CONNECTOR_DIR / "hermes_bcn.py").read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 11))

    async def test_websocket_connect_omits_proxy_for_websockets_14_signature(self) -> None:
        calls = []

        async def websockets_14_connect(uri):
            calls.append(uri)
            return "connection"

        result = await _open_websocket("ws://127.0.0.1:1", websockets_14_connect)

        self.assertEqual("connection", result)
        self.assertEqual(["ws://127.0.0.1:1"], calls)

    async def test_reconnect_uses_and_persists_rotated_token_and_heartbeats(self) -> None:
        connections: list[dict] = []
        heartbeat_seen = asyncio.Event()

        async def bcn_server(websocket) -> None:
            connect_frame = json.loads(await websocket.recv())
            connections.append(connect_frame)
            index = len(connections)
            replacement = "rotated-once" if index == 1 else "rotated-twice"
            await websocket.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": connect_frame["id"],
                        "ok": True,
                        "payload": {
                            "bot_uuid": "bot-123",
                            "token": replacement,
                            "protocol_version": 2,
                        },
                    }
                )
            )
            if index == 1:
                await websocket.send('{"bot_token":"must-not-appear-in-logs"')
                await websocket.close()
                return

            while True:
                frame = json.loads(await websocket.recv())
                if frame.get("method") == "bot.status":
                    heartbeat_seen.set()
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "res",
                                "id": frame["id"],
                                "ok": True,
                                "payload": {},
                            }
                        )
                    )
                    await websocket.wait_closed()
                    return

        server = await serve(bcn_server, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, server)
        port = server.sockets[0].getsockname()[1]
        state_path = Path(self.tempdir.name) / "session.json"
        store = AtomicJsonStore(state_path)
        store.save(
            {
                "bcs_url": f"ws://127.0.0.1:{port}",
                "bot_uuid": "bot-123",
                "bot_token": "initial-token",
                "keep": "untouched",
            }
        )
        stop = asyncio.Event()
        client = BcsClient(
            url=f"ws://127.0.0.1:{port}",
            bot_id="bot-123",
            token="initial-token",
            credential_store=store,
            heartbeat_interval=0.01,
            reconnect_delays=(0.01, 0.02),
        )

        with self.assertLogs("hermes_bcn", logging.WARNING) as captured:
            task = asyncio.create_task(client.run(lambda _frame: None, stop_event=stop))
            await asyncio.wait_for(heartbeat_seen.wait(), timeout=2)
            stop.set()
            await asyncio.wait_for(task, timeout=2)

        self.assertEqual(2, len(connections))
        self.assertEqual(
            {
                "bot_id": "bot-123",
                "token": "initial-token",
                "protocol_version": 2,
            },
            connections[0]["params"],
        )
        self.assertEqual("rotated-once", connections[1]["params"]["token"])
        persisted = store.load()
        self.assertEqual("rotated-twice", persisted["bot_token"])
        self.assertEqual("untouched", persisted["keep"])
        self.assertNotIn("must-not-appear-in-logs", "\n".join(captured.output))

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
