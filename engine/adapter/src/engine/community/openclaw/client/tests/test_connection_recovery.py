"""Connection recovery must never replay application requests."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from engine.community.openclaw.client import gateway_client as module
from engine.community.openclaw.client import gateway_connection as connection
from engine.community.openclaw.config import OpenClawConfig


class Socket:
    def __init__(self):
        self.frames = [
            {"type": "event", "event": "connect.challenge", "payload": {"nonce": "test"}},
            {
                "type": "res", "id": "connect", "ok": True,
                "payload": {
                    "protocol": 3, "server": {"version": "test", "connId": "test"},
                    "features": {"methods": [], "events": []}, "policy": {},
                },
            },
        ]
        self.sent = []
        self.close = AsyncMock()
        self.end = asyncio.Event()
        self.started = asyncio.Event()

    async def recv(self):
        return json.dumps(self.frames.pop(0))

    async def send(self, value):
        self.sent.append(json.loads(value))

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.started.set()
        await self.end.wait()
        raise StopAsyncIteration


@pytest.fixture
def setup_client(monkeypatch):
    client = module.OpenClawGatewayClient(
        config=OpenClawConfig(gateway_url="ws://127.0.0.1:0")
    )
    socket = Socket()
    dial = AsyncMock(return_value=socket)
    monkeypatch.setattr(connection.websockets, "connect", dial)
    monkeypatch.setattr(connection, "_load_or_create_device_keypair", lambda _: (None, None, None))
    return client, socket, dial


@pytest.mark.asyncio
async def test_stale_connected_flag_really_reconnects(setup_client):
    client, socket, dial = setup_client
    client._connected = True
    client._hello = object()
    await client.connect()
    try:
        assert client.connected
        assert client._ws is socket
        dial.assert_awaited_once()
        assert [frame["method"] for frame in socket.sent] == ["connect"]
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_concurrent_pool_checkout_handshakes_once_and_preserves_listeners(setup_client):
    client, socket, dial = setup_client
    pool = module.ConnectionPool(size=1)
    pool._clients = [client]
    pool._initialized = True
    listener = lambda event: None
    client.on_event("chat", listener)
    clients = await asyncio.gather(*(pool.get() for _ in range(20)))
    try:
        assert all(c is client and c.connected for c in clients)
        dial.assert_awaited_once()
        assert [frame["method"] for frame in socket.sent] == ["connect"]
        assert client._event_listeners["chat"] == [listener]
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("bad handshake"), asyncio.TimeoutError()])
async def test_failed_handshake_cleans_state_then_can_retry(setup_client, failure):
    client, socket, dial = setup_client
    socket.recv = AsyncMock(side_effect=failure)
    with pytest.raises((ValueError, ConnectionError)):
        await client.connect()
    assert not client.connected
    assert client._ws is None
    assert client.hello is None
    socket.close.assert_awaited_once()
    replacement = Socket()
    dial.return_value = replacement
    await client.connect()
    assert client.connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_cancelled_handshake_cleans_socket_and_releases_lock(setup_client):
    client, socket, dial = setup_client
    entered = asyncio.Event()

    async def wait_forever():
        entered.set()
        await asyncio.Event().wait()

    socket.recv = wait_forever
    task = asyncio.create_task(client.connect())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    socket.close.assert_awaited_once()
    assert client._ws is None
    assert not client.connected
    dial.return_value = Socket()
    await client.connect()
    await client.disconnect()


@pytest.mark.asyncio
async def test_disconnect_serializes_with_handshake(setup_client):
    client, socket, dial = setup_client
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def blocked_dial(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return socket

    dial.side_effect = blocked_dial
    connecting = asyncio.create_task(client.connect())
    await entered.wait()
    disconnecting = asyncio.create_task(client.disconnect())
    await asyncio.sleep(0)
    assert not disconnecting.done()
    proceed.set()
    await asyncio.gather(connecting, disconnecting)
    assert not client.connected
    assert client._ws is None
    assert client._recv_task is None
    socket.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_receive_loop_cannot_mark_new_socket_disconnected(setup_client):
    client, old_socket, _ = setup_client
    client._ws = old_socket
    client._connected = True
    old_loop = asyncio.create_task(client._recv_loop())
    await old_socket.started.wait()
    client._ws = Socket()
    old_socket.end.set()
    await old_loop
    assert client.connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_recovery_fails_old_pending_chat_without_replaying_it(setup_client):
    client, old_socket, dial = setup_client
    await client.connect()
    pending = asyncio.create_task(client.send_request("chat.send", {"message": "hello"}))
    await asyncio.sleep(0)
    assert [frame["method"] for frame in old_socket.sent] == ["connect", "chat.send"]
    old_socket.end.set()
    await client._recv_task
    replacement = Socket()
    dial.return_value = replacement
    await client.connect()
    with pytest.raises(ConnectionError, match="Connection closed"):
        await pending
    assert [frame["method"] for frame in replacement.sent] == ["connect"]
    assert client._pending_requests == {}
    await client.disconnect()


@pytest.mark.asyncio
async def test_close_failure_still_resets_client(setup_client):
    client, socket, _ = setup_client
    await client.connect()
    socket.close.side_effect = OSError("close failed")
    await client.disconnect()
    assert not client.connected
    assert client._ws is None
    assert client.hello is None
    assert not client._closing


@pytest.mark.asyncio
async def test_pool_skips_client_that_is_unavailable_after_connect(setup_client):
    client, _, _ = setup_client
    client.connect = AsyncMock()
    pool = module.ConnectionPool(size=1)
    pool._clients = [client]
    pool._initialized = True
    with pytest.raises(ConnectionError, match="All pool connections failed"):
        await pool.get()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), OSError("refused")])
async def test_dial_failure_releases_lock_for_later_attempt(setup_client, error):
    client, socket, dial = setup_client
    dial.side_effect = error
    with pytest.raises(ConnectionError):
        await client.connect()
    assert client._ws is None and client.hello is None
    dial.side_effect = None
    await client.connect()
    assert client.connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_handshake_timeout_cleans_up(setup_client):
    client, socket, _ = setup_client
    socket.recv = AsyncMock(side_effect=[json.dumps(socket.frames[0]), TimeoutError()])
    with pytest.raises(ConnectionError, match="Handshake timeout"):
        await client.connect()
    socket.close.assert_awaited_once()
    assert client._ws is None and not client.connected


@pytest.mark.asyncio
async def test_rejected_handshake_cleans_up(setup_client):
    client, socket, _ = setup_client
    socket.frames[1] = {
        "type": "res", "id": "connect", "ok": False,
        "error": {"code": "INVALID_REQUEST", "message": "denied",
                  "details": {"code": "DEVICE_AUTH_TEST", "reason": "test"}},
    }
    with pytest.raises(ConnectionError, match="Handshake failed: denied"):
        await client.connect()
    socket.close.assert_awaited_once()
    assert client._ws is None and client.hello is None


@pytest.mark.asyncio
async def test_device_signature_and_key_reload_are_unchanged(monkeypatch, tmp_path):
    key_file = tmp_path / "device.pem"
    socket = Socket()
    socket.frames[1]["payload"]["auth"] = {"role": "operator", "scopes": ["operator.read"]}
    monkeypatch.setattr(connection.websockets, "connect", AsyncMock(return_value=socket))
    client = module.OpenClawGatewayClient(
        config=OpenClawConfig(gateway_url="ws://127.0.0.1:0", gateway_token="test-only"),
        device_key_file=key_file, upstream_headers={"X-Test": "test"},
    )
    await client.connect()
    try:
        params = socket.sent[0]["params"]
        assert params["device"]["nonce"] == "test"
        assert params["device"]["signature"]
        _, public_key, device_id = connection._load_or_create_device_keypair(key_file)
        assert params["device"]["id"] == device_id
        assert params["device"]["publicKey"] == public_key
        assert key_file.stat().st_mode & 0o777 == 0o600
        key_file.write_text("invalid key for recovery test")
        _, _, new_id = connection._load_or_create_device_keypair(key_file)
        assert new_id != device_id
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_response_and_event_dispatch_survive_reconnect(setup_client):
    client, socket, dial = setup_client
    received = []
    client.on_event("chat", lambda event: received.append(event.payload))
    await client.connect()
    await client.disconnect()

    class ReplySocket(Socket):
        def __aiter__(self):
            async def frames():
                yield "{invalid"
                yield json.dumps({"type": "res", "id": "rpc", "ok": True, "payload": {}})
                yield json.dumps({"type": "event", "event": "chat", "payload": {"state": "final"}})
            return frames()

    dial.return_value = ReplySocket()
    await client.connect()
    future = asyncio.get_running_loop().create_future()
    client._pending_requests["rpc"] = future
    await client._recv_task
    assert (await future).ok
    assert received == [{"state": "final"}]
    assert not client.connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_failed_receive_task_does_not_prevent_socket_cleanup(setup_client):
    client, socket, _ = setup_client

    async def fail():
        raise RuntimeError("receive task failed")

    client._ws = socket
    client._recv_task = asyncio.create_task(fail())
    await asyncio.sleep(0)
    await client.disconnect()
    socket.close.assert_awaited_once()
    assert client._ws is None and client._recv_task is None


@pytest.mark.asyncio
async def test_cancelling_disconnect_still_closes_detached_socket(setup_client):
    client, socket, _ = setup_client
    started, cancelling = asyncio.Event(), asyncio.Event()

    async def slow_receiver():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await asyncio.Event().wait()

    client._ws = socket
    client._recv_task = asyncio.create_task(slow_receiver())
    await started.wait()
    task = asyncio.create_task(client.disconnect())
    await cancelling.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    socket.close.assert_awaited_once()
    assert client._ws is None and client._recv_task is None
    assert not client._closing
