from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.server import serve


CONNECTOR_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONNECTOR_DIR))

from hermes_bcn import (  # noqa: E402
    AtomicJsonStore,
    BcsClient,
    HermesBcnBridge,
    HermesClient,
)


def chat_request(request_id: str, group: str, text: str) -> dict:
    return {
        "type": "req",
        "id": request_id,
        "method": "chat.send",
        "params": {
            "session_key": f"session-{group}",
            "bcs_group_id": group,
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
                "timestamp": 1000,
            },
            "channel": {"source": "webui", "user_id": "alice"},
        },
    }


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    async def test_end_to_end_ack_observations_delta_final_history_and_unknown(self) -> None:
        hermes_requests: list[dict] = []
        prompt_text = None

        async def hermes_server(websocket) -> None:
            nonlocal prompt_text
            async for raw in websocket:
                request = json.loads(raw)
                hermes_requests.append(request)
                method = request["method"]
                if method == "session.create":
                    result = {
                        "session_id": "live-1",
                        "stored_session_id": "stored-1",
                    }
                elif method == "prompt.submit":
                    prompt_text = request["params"]["text"]
                    result = {"status": "streaming"}
                elif method == "session.history":
                    result = {
                        "messages": [
                            {"role": "user", "text": "old", "timestamp": 1},
                            {
                                "role": "assistant",
                                "content": [{"type": "text", "text": "answer"}],
                                "timestamp": 2,
                            },
                        ]
                    }
                else:
                    raise AssertionError(method)
                await websocket.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": request["id"], "result": result}
                    )
                )
                if method == "prompt.submit":
                    for event_type, payload in (
                        ("message.delta", {"text": "A"}),
                        ("message.delta", {"text": "B"}),
                        (
                            "message.complete",
                            {
                                "text": "Answer",
                                "status": "complete",
                                "usage": {"input": 4, "output": 2},
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
                                        "session_id": "live-1",
                                        "payload": payload,
                                    },
                                }
                            )
                        )

        hermes = await serve(hermes_server, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, hermes)
        hermes_port = hermes.sockets[0].getsockname()[1]
        bcn_result = asyncio.get_running_loop().create_future()

        async def bcn_server(websocket) -> None:
            connect = json.loads(await websocket.recv())
            await self._accept_connect(websocket, connect)
            inject = {
                "type": "req",
                "id": "inject-1",
                "method": "chat.inject",
                "params": {
                    "session_key": "session-group-1",
                    "bcs_group_id": "group-1",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "silent context"}],
                    },
                    "channel": {"user_id": "observer"},
                },
            }
            await websocket.send(json.dumps(inject))
            inject_ack = json.loads(await websocket.recv())
            await websocket.send(json.dumps(chat_request("send-1", "group-1", "question")))
            send_ack = json.loads(await websocket.recv())
            events = []
            while not events or events[-1].get("payload", {}).get("state") != "final":
                events.append(json.loads(await websocket.recv()))
            await websocket.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": "history-1",
                        "method": "chat.history",
                        "params": {"session_key": "session-group-1", "limit": 1},
                    }
                )
            )
            history = json.loads(await websocket.recv())
            await websocket.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": "unknown-1",
                        "method": "chat.future",
                        "params": {},
                    }
                )
            )
            unknown = json.loads(await websocket.recv())
            bcn_result.set_result((inject_ack, send_ack, events, history, unknown))
            await websocket.wait_closed()

        bcn = await serve(bcn_server, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, bcn)
        bcn_port = bcn.sockets[0].getsockname()[1]
        state_store = AtomicJsonStore(Path(self.tempdir.name) / "groups.json")
        stop, client, hermes_client, task = self._start_connector(
            bcn_port, hermes_port, state_store
        )
        self.addAsyncCleanup(self._stop_connector, stop, client, hermes_client, task)

        inject_ack, send_ack, events, history, unknown = await asyncio.wait_for(
            bcn_result, timeout=2
        )
        self.assertTrue(inject_ack["ok"])
        self.assertTrue(send_ack["ok"])
        self.assertIn("run_id", send_ack["payload"])
        self.assertEqual(["delta", "delta", "final"], [e["payload"]["state"] for e in events])
        self.assertEqual(
            ["A", "AB"],
            [
                e["payload"]["message"]["content"][0]["text"]
                for e in events[:2]
            ],
        )
        self.assertEqual({"input": 4, "output": 2}, events[-1]["payload"]["usage"])
        self.assertIn("observer: silent context", prompt_text)
        self.assertTrue(prompt_text.endswith("question"))
        self.assertEqual("answer", history["payload"]["messages"][0]["content"])
        self.assertEqual("unknown_method", unknown["error"]["code"])
        persisted_group = state_store.load()["groups"]["group-1"]
        self.assertEqual("stored-1", persisted_group["stored_session_id"])
        self.assertEqual([], persisted_group["observations"])
        self.assertEqual(
            "live-1",
            next(r for r in hermes_requests if r["method"] == "session.history")[
                "params"
            ]["session_id"],
        )

    async def test_persisted_session_is_resumed_and_abort_is_terminal(self) -> None:
        prompt_started = asyncio.Event()
        interrupted = asyncio.Event()
        methods: list[str] = []

        async def hermes_server(websocket) -> None:
            async for raw in websocket:
                request = json.loads(raw)
                method = request["method"]
                methods.append(method)
                if method == "session.resume":
                    self.assertEqual("stored-resume", request["params"]["session_id"])
                    result = {"session_id": "live-resumed", "session_key": "stored-resume"}
                elif method == "prompt.submit":
                    result = {"status": "streaming"}
                    prompt_started.set()
                elif method == "session.interrupt":
                    self.assertEqual("live-resumed", request["params"]["session_id"])
                    result = {"status": "interrupted"}
                    interrupted.set()
                else:
                    raise AssertionError(method)
                await websocket.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": request["id"], "result": result}
                    )
                )

        hermes = await serve(hermes_server, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, hermes)
        hermes_port = hermes.sockets[0].getsockname()[1]
        bcn_result = asyncio.get_running_loop().create_future()

        async def bcn_server(websocket) -> None:
            connect = json.loads(await websocket.recv())
            await self._accept_connect(websocket, connect)
            await websocket.send(json.dumps(chat_request("send-resume", "group-r", "wait")))
            ack = json.loads(await websocket.recv())
            await asyncio.wait_for(prompt_started.wait(), timeout=1)
            await websocket.send(
                json.dumps(
                    {
                        "type": "event",
                        "event": "chat.abort",
                        "payload": {
                            "session_key": "session-group-r",
                            "run_id": ack["payload"]["run_id"],
                        },
                    }
                )
            )
            terminal = json.loads(await websocket.recv())
            bcn_result.set_result((ack, terminal))
            await websocket.wait_closed()

        bcn = await serve(bcn_server, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, bcn)
        bcn_port = bcn.sockets[0].getsockname()[1]
        state_store = AtomicJsonStore(Path(self.tempdir.name) / "groups.json")
        state_store.save(
            {
                "groups": {
                    "group-r": {
                        "session_key": "session-group-r",
                        "stored_session_id": "stored-resume",
                        "observations": [],
                    }
                }
            }
        )
        stop, client, hermes_client, task = self._start_connector(
            bcn_port, hermes_port, state_store
        )
        self.addAsyncCleanup(self._stop_connector, stop, client, hermes_client, task)

        ack, terminal = await asyncio.wait_for(bcn_result, timeout=2)
        self.assertTrue(ack["ok"])
        self.assertEqual("aborted", terminal["payload"]["state"])
        self.assertEqual("aborted", terminal["payload"]["stop_reason"])
        self.assertTrue(interrupted.is_set())
        self.assertEqual(
            ["session.resume", "prompt.submit", "session.interrupt"], methods
        )

    async def test_same_group_serializes_while_other_group_runs_concurrently(self) -> None:
        prompt_order: list[str] = []
        first_two_started = asyncio.Event()
        release = asyncio.Event()
        send_lock = asyncio.Lock()
        session_counter = 0

        async def send_json(websocket, frame: dict) -> None:
            async with send_lock:
                await websocket.send(json.dumps(frame))

        async def finish_prompt(websocket, session_id: str, text: str) -> None:
            if text in {"A1", "B1"}:
                await release.wait()
            await send_json(
                websocket,
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "message.complete",
                        "session_id": session_id,
                        "payload": {"text": f"done-{text}", "status": "complete"},
                    },
                },
            )

        async def hermes_server(websocket) -> None:
            nonlocal session_counter
            background = set()
            try:
                async for raw in websocket:
                    request = json.loads(raw)
                    if request["method"] == "session.create":
                        session_counter += 1
                        result = {
                            "session_id": f"live-{session_counter}",
                            "stored_session_id": f"stored-{session_counter}",
                        }
                    elif request["method"] == "prompt.submit":
                        text = request["params"]["text"]
                        prompt_order.append(text)
                        if {"A1", "B1"}.issubset(prompt_order):
                            first_two_started.set()
                        result = {"status": "streaming"}
                    else:
                        raise AssertionError(request["method"])
                    await send_json(
                        websocket,
                        {"jsonrpc": "2.0", "id": request["id"], "result": result},
                    )
                    if request["method"] == "prompt.submit":
                        task = asyncio.create_task(
                            finish_prompt(websocket, request["params"]["session_id"], text)
                        )
                        background.add(task)
                        task.add_done_callback(background.discard)
            finally:
                for task in background:
                    task.cancel()

        hermes = await serve(hermes_server, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, hermes)
        hermes_port = hermes.sockets[0].getsockname()[1]
        bcn_done = asyncio.Event()

        async def bcn_server(websocket) -> None:
            connect = json.loads(await websocket.recv())
            await self._accept_connect(websocket, connect)
            for request in (
                chat_request("a1", "group-a", "A1"),
                chat_request("a2", "group-a", "A2"),
                chat_request("b1", "group-b", "B1"),
            ):
                await websocket.send(json.dumps(request))
            ack_ids = set()
            final_count = 0
            while final_count < 3:
                frame = json.loads(await websocket.recv())
                if frame["type"] == "res":
                    ack_ids.add(frame["id"])
                elif frame.get("payload", {}).get("state") == "final":
                    final_count += 1
            self.assertEqual({"a1", "a2", "b1"}, ack_ids)
            bcn_done.set()
            await websocket.wait_closed()

        bcn = await serve(bcn_server, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, bcn)
        bcn_port = bcn.sockets[0].getsockname()[1]
        state_store = AtomicJsonStore(Path(self.tempdir.name) / "groups.json")
        stop, client, hermes_client, task = self._start_connector(
            bcn_port, hermes_port, state_store
        )
        self.addAsyncCleanup(self._stop_connector, stop, client, hermes_client, task)

        await asyncio.wait_for(first_two_started.wait(), timeout=2)
        self.assertNotIn("A2", prompt_order)
        release.set()
        await asyncio.wait_for(bcn_done.wait(), timeout=2)
        self.assertGreater(prompt_order.index("A2"), prompt_order.index("A1"))

    def _start_connector(self, bcn_port, hermes_port, state_store):
        stop = asyncio.Event()
        credentials = AtomicJsonStore(Path(self.tempdir.name) / f"session-{bcn_port}.json")
        credentials.save({"bot_uuid": "bot-1", "bot_token": "bot-token"})
        bcs = BcsClient(
            url=f"ws://127.0.0.1:{bcn_port}",
            bot_id="bot-1",
            token="bot-token",
            credential_store=credentials,
            heartbeat_interval=60,
            reconnect_delays=(0.01,),
        )
        hermes = HermesClient(f"http://127.0.0.1:{hermes_port}", "dashboard-token")
        bridge = HermesBcnBridge(bcs, hermes, state_store)
        task = asyncio.create_task(bcs.run(bridge.handle_frame, stop_event=stop))
        return stop, bcs, hermes, task

    @staticmethod
    async def _accept_connect(websocket, connect) -> None:
        await websocket.send(
            json.dumps(
                {
                    "type": "res",
                    "id": connect["id"],
                    "ok": True,
                    "payload": {
                        "bot_uuid": "bot-1",
                        "token": "bot-token",
                        "protocol_version": 2,
                    },
                }
            )
        )

    @staticmethod
    async def _stop_connector(stop, bcs, hermes, task) -> None:
        stop.set()
        await bcs.close()
        await hermes.close()
        try:
            await asyncio.wait_for(task, timeout=1)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
