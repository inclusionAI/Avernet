from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

from websockets.asyncio.server import serve


CONNECTOR_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONNECTOR_DIR))

from hermes_bcn import HermesClient, HermesRpcError  # noqa: E402


class HermesGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_rpc_methods_and_prompt_stream(self) -> None:
        requests: list[dict] = []
        request_path = None

        async def gateway(websocket) -> None:
            nonlocal request_path
            request_path = websocket.request.path
            await websocket.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {"type": "gateway.ready", "payload": {}},
                    }
                )
            )
            async for raw in websocket:
                request = json.loads(raw)
                requests.append(request)
                method = request["method"]
                if method == "session.create":
                    result = {
                        "session_id": "live-created",
                        "stored_session_id": "stored-1",
                    }
                elif method == "session.resume":
                    result = {
                        "session_id": "live-resumed",
                        "session_key": "stored-1",
                    }
                elif method == "session.history":
                    result = {
                        "messages": [
                            {"role": "user", "text": "hello"},
                            {"role": "assistant", "text": "world"},
                        ]
                    }
                elif method == "session.interrupt":
                    result = {"status": "interrupted"}
                elif method == "prompt.submit":
                    result = {"status": "streaming"}
                else:
                    raise AssertionError(method)
                await websocket.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": request["id"], "result": result}
                    )
                )
                if method == "prompt.submit":
                    for event_type, payload in (
                        ("message.delta", {"text": "hel"}),
                        (
                            "message.complete",
                            {
                                "text": "hello",
                                "status": "complete",
                                "usage": {"input": 2, "output": 3},
                            },
                        ),
                    ):
                        await websocket.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "event",
                                    "params": {
                                        "type": event_type,
                                        "session_id": "live-resumed",
                                        "payload": payload,
                                    },
                                }
                            )
                        )

        server = await serve(gateway, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, server)
        port = server.sockets[0].getsockname()[1]
        client = HermesClient(f"http://127.0.0.1:{port}", "dashboard secret")
        self.addAsyncCleanup(client.close)

        created = await client.create_session(cwd="/workspace")
        resumed = await client.resume_session("stored-1")
        history = await client.session_history("live-resumed")
        stream = await client.submit_prompt("live-resumed", "question")
        events = [event async for event in stream]
        interrupted = await client.interrupt_session("live-resumed")

        self.assertEqual("/api/ws?token=dashboard+secret", request_path)
        self.assertEqual("stored-1", created["stored_session_id"])
        self.assertEqual("live-resumed", resumed["session_id"])
        self.assertEqual("world", history["messages"][1]["text"])
        self.assertEqual(["message.delta", "message.complete"], [e["type"] for e in events])
        self.assertEqual("interrupted", interrupted["status"])
        self.assertEqual(
            {"source": "avernet-bcn", "cwd": "/workspace"}, requests[0]["params"]
        )
        self.assertEqual(
            {"session_id": "stored-1", "source": "avernet-bcn"},
            requests[1]["params"],
        )
        self.assertEqual(
            {"session_id": "live-resumed", "text": "question"},
            next(r for r in requests if r["method"] == "prompt.submit")["params"],
        )

    async def test_request_correlation_and_rpc_errors(self) -> None:
        async def gateway(websocket) -> None:
            first = json.loads(await websocket.recv())
            second = json.loads(await websocket.recv())
            await websocket.send(
                json.dumps(
                    {"jsonrpc": "2.0", "id": second["id"], "result": {"value": 2}}
                )
            )
            await websocket.send(
                json.dumps(
                    {"jsonrpc": "2.0", "id": first["id"], "result": {"value": 1}}
                )
            )
            third = json.loads(await websocket.recv())
            await websocket.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": third["id"],
                        "error": {"code": 4007, "message": "session not found"},
                    }
                )
            )

        server = await serve(gateway, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, server)
        port = server.sockets[0].getsockname()[1]
        client = HermesClient(f"ws://127.0.0.1:{port}/api/ws", "token")
        self.addAsyncCleanup(client.close)

        one, two = await asyncio.gather(
            client.request("test.one"), client.request("test.two")
        )
        self.assertEqual(({"value": 1}, {"value": 2}), (one, two))
        with self.assertRaises(HermesRpcError) as raised:
            await client.resume_session("missing")
        self.assertEqual(4007, raised.exception.code)

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
