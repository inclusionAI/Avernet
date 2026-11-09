"""Exercise subscription handshakes through the real receive loop."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.server import serve

from engine.community.openclaw.client import gateway_connection as connection
from engine.community.openclaw.client.gateway_client import ConnectionPool
from engine.community.openclaw.client.tests.test_gateway_client import _make_client
from engine.community.openclaw.client.tests.test_connection_recovery import Socket

pytestmark = pytest.mark.asyncio


class SubscriptionSocket(Socket):
    def __init__(self, response=None):
        super().__init__()
        self.frames[1]["payload"]["features"]["methods"] = ["sessions.subscribe"]
        self.queue = asyncio.Queue()
        self.response = response or {"ok": True, "payload": {"subscribed": True}}

    async def send(self, value):
        await super().send(value)
        frame = self.sent[-1]
        if frame["method"] == "sessions.subscribe":
            await self.queue.put(json.dumps({"type": "res", "id": frame["id"], **self.response}))

    async def __anext__(self):
        return await self.queue.get()


@pytest.fixture
def gateway(monkeypatch):
    client = _make_client()
    socket = SubscriptionSocket()
    dial = AsyncMock(return_value=socket)
    monkeypatch.setattr(connection.websockets, "connect", dial)
    monkeypatch.setattr(connection, "_load_or_create_device_keypair", lambda _: (None, None, None))
    return client, socket, dial


async def test_shared_connection_subscribes_once_and_reconnects(gateway):
    client, socket, dial = gateway
    await asyncio.gather(*(client.connect() for _ in range(5)))
    try:
        assert [frame["method"] for frame in socket.sent] == ["connect", "sessions.subscribe"]
        assert socket.sent[1]["params"] == {}
        assert client.connected
        await client.disconnect()
        replacement = SubscriptionSocket()
        dial.return_value = replacement
        await client.connect()
        assert [frame["method"] for frame in replacement.sent] == ["connect", "sessions.subscribe"]
    finally:
        await client.disconnect()


@pytest.mark.parametrize("response", [
    {"ok": False, "error": {"code": "FORBIDDEN", "message": "denied"}},
    {"ok": True, "payload": {"subscribed": False}},
    {"ok": True, "payload": {}},
])
async def test_advertised_subscription_failure_cleans_connection(gateway, response):
    client, socket, _ = gateway
    socket.response = response
    try:
        with pytest.raises(ConnectionError, match="subscription"):
            await client.connect()
        assert not client.connected
        assert client._ws is None
        assert client._recv_task is None
        assert client._pending_requests == {}
    finally:
        await client.disconnect()


async def test_subscription_timeout_cleans_connection_without_logging_error_body(gateway, caplog):
    client, _, _ = gateway
    client.send_request = AsyncMock(side_effect=TimeoutError("private-auth-value"))
    with pytest.raises(TimeoutError):
        await client.connect()
    assert not client.connected and client._ws is None
    assert client._recv_task is None
    assert "status=failed" in caplog.text
    assert "private-auth-value" not in caplog.text


async def test_cancelled_subscription_cleans_connection(gateway):
    client, _, _ = gateway
    entered = asyncio.Event()

    async def pending_subscription(method, params):
        entered.set()
        await asyncio.Event().wait()

    client.send_request = pending_subscription
    task = asyncio.create_task(client.connect())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.connected and client._ws is None
    assert client._recv_task is None


async def test_pool_does_not_expose_connection_until_subscription_succeeds(gateway):
    client, _, _ = gateway
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def subscribe(method, params):
        from engine.community.openclaw.protocol import ResponseFrame

        entered.set()
        await proceed.wait()
        return ResponseFrame.ok_response("subscription", {"subscribed": True})

    client.send_request = subscribe
    connecting = asyncio.create_task(client.connect())
    await asyncio.wait_for(entered.wait(), 1)
    pool = ConnectionPool(size=1)
    pool._clients, pool._initialized = [client], True
    checkout = asyncio.create_task(pool.get())
    try:
        finished, _ = await asyncio.wait({checkout}, timeout=0.01)
        assert not finished, "The pool handed out a connection before subscription succeeded"
        proceed.set()
        await connecting
        assert await checkout is client
    finally:
        proceed.set()
        await asyncio.gather(connecting, checkout, return_exceptions=True)
        await pool.shutdown()


async def test_announce_spawn_over_real_socket_waits_for_second_child(monkeypatch):
    """Catch the live failure without depending on a model or a production Bot."""
    monkeypatch.setattr(connection, "_load_or_create_device_keypair", lambda _: (None, None, None))
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    parent = "agent:main:wire-parent"
    child_a, child_b = "agent:main:subagent:a", "agent:main:subagent:b"
    announce_a, announce_b = f"announce:v1:{child_a}:a", f"announce:v1:{child_b}:b"
    allow_b, observed_a = asyncio.Event(), asyncio.Event()

    async def server(socket):
        await socket.send(json.dumps({
            "type": "event", "event": "connect.challenge", "payload": {"nonce": "test"},
        }))
        request = json.loads(await socket.recv())
        await socket.send(json.dumps({
            "type": "res", "id": request["id"], "ok": True,
            "payload": {"protocol": 3, "server": {"version": "test", "connId": "wire"},
                        "features": {"methods": ["sessions.subscribe"], "events": []}, "policy": {}},
        }))
        subscription = json.loads(await socket.recv())
        assert subscription["method"] == "sessions.subscribe"
        await socket.send(json.dumps({
            "type": "res", "id": subscription["id"], "ok": True, "payload": {"subscribed": True},
        }))
        chat = json.loads(await socket.recv())
        assert chat["method"] == "chat.send"
        await socket.send(json.dumps({
            "type": "res", "id": chat["id"], "ok": True,
            "payload": {"runId": "p", "status": "started"},
        }))

        async def emit(event, run, session=parent, **data):
            await socket.send(json.dumps({"type": "event", "event": event, "payload": {
                "runId": run, "sessionKey": session, **data,
            }}))

        def accepted(child, run):
            return {"name": "sessions_spawn", "phase": "result", "result": {"details": {
                "status": "accepted", "childSessionKey": child, "runId": run,
            }}}

        await emit("agent", "p", stream="tool", data=accepted(child_a, "a"))
        await emit("chat", "p", state="final", text="waiting A")
        await emit("chat", "a", child_a, state="final", text="private A")
        await emit("session.tool", announce_a, stream="tool", data=accepted(child_b, "b"))
        await emit("chat", announce_a, state="final", text="B is still running")
        await allow_b.wait()
        await emit("chat", "b", child_b, state="final", text="private B")
        await emit("chat", announce_b, state="final", text="complete A and B")
        await socket.wait_closed()

    async with serve(server, "127.0.0.1", 0) as listener:
        client = _make_client()
        client._config.gateway_url = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"

        def observe(event):
            if event.payload.get("runId") == announce_a and event.payload.get("state") == "final":
                observed_a.set()

        client.on_event("*", observe)
        task = None
        try:
            await client.connect()

            async def collect():
                return [event async for event in client.chat_stream(
                    parent, "coordinate children", idempotency_key="p", timeout_ms=1000,
                )]

            task = asyncio.create_task(collect())
            await asyncio.wait_for(observed_a.wait(), 2)
            finished, _ = await asyncio.wait({task}, timeout=0.01)
            assert not finished
            allow_b.set()
            events = await asyncio.wait_for(task, 2)
            assert [e["text"] for e in events if e.get("state") == "final"] == ["complete A and B"]
            assert all(e.get("sessionKey") == parent for e in events)
        finally:
            allow_b.set()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            client.off_event("*", observe)
            await client.disconnect()
