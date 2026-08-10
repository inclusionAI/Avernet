"""Unit tests for BotWebSocketClient (async version).

Covers:
- __init__: defaults auto-gen client_id, custom values
- _next_request_id: increments sequentially
- connect: establishes ws, starts recv_task, completes handshake
- close: cancels recv_task, closes ws, resets flags
- _send_request: request_id assignment, pending dict entry, response matching
- _handle_message: parses JSON, routes to pending requests or event handlers
- chat_send/chat_abort/session_reset: delegate to _send_request
- on_event: register event handlers
- properties: connected, server_info, features
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.core.service.bot_run._bot_websocket_client import (
    BotWebSocketClient,
    ChatRequestError,
    _is_loopback_websocket_uri,
)

# ==================== Fixtures ====================


@pytest.fixture
def client():
    """Create a client instance."""
    return BotWebSocketClient(uri="wss://test.example.com/ws")


@pytest.fixture
def client_custom():
    """Create a client with custom parameters."""
    return BotWebSocketClient(
        uri="wss://custom.example.com/ws",
        client_id="my-custom-id",
        client_version="2.0.0",
        platform="go",
        mode="server",
        headers={"Authorization": "Bearer token123"},
    )


# ==================== Tests: __init__ ====================


class TestInit:
    """Tests for BotWebSocketClient.__init__."""

    # [单测用例]测试场景：默认 client_id 自动生成
    def test_default_client_id_is_auto_generated(self, client):
        """client_id is auto-generated as 'client-<8 hex chars>' when not provided."""
        assert client.client_id.startswith("client-")
        assert len(client.client_id) == len("client-") + 8

    # [单测用例]测试场景：自定义 client_id 保留
    def test_custom_client_id_is_preserved(self, client_custom):
        """Custom client_id is used as-is."""
        assert client_custom.client_id == "my-custom-id"

    # [单测用例]测试场景：默认参数值
    def test_default_values(self, client):
        """Defaults for version, platform, mode, headers."""
        assert client.client_version == "1.0.0"
        assert client.platform == "python"
        assert client.mode == "cli"
        assert client.headers == {}

    # [单测用例]测试场景：自定义参数值
    def test_custom_values(self, client_custom):
        """Custom values are stored."""
        assert client_custom.client_version == "2.0.0"
        assert client_custom.platform == "go"
        assert client_custom.mode == "server"
        assert client_custom.headers == {"Authorization": "Bearer token123"}

    # [单测用例]测试场景：初始状态标志
    def test_initial_state_flags_are_false(self, client):
        """All connection state flags start as False/None."""
        assert client._connected is False
        assert client._handshake_complete is False
        assert client._ws is None
        assert client._recv_task is None
        assert client._request_id == 0
        assert client._pending_requests == {}
        assert client._event_handlers == {}
        assert client._server_info is None
        assert client._features is None

    # [单测用例]测试场景：属性返回正确状态
    def test_properties_return_correct_state(self, client):
        """connected/server_info/features match internal state."""
        client._connected = False
        client._handshake_complete = False
        assert client.connected is False
        assert client.server_info is None
        assert client.features is None

        client._connected = True
        client._handshake_complete = True
        client._server_info = {"version": "1.0"}
        client._features = {"chat": True}
        assert client.connected is True
        assert client.server_info == {"version": "1.0"}
        assert client.features == {"chat": True}

    # [单测用例]测试场景：connected 需要两个标志都为 True
    def test_connected_only_true_when_both_flags_set(self, client):
        """connected is True only when _connected AND _handshake_complete are True."""
        client._connected = True
        client._handshake_complete = False
        assert client.connected is False

        client._connected = False
        client._handshake_complete = True
        assert client.connected is False

        client._connected = True
        client._handshake_complete = True
        assert client.connected is True

    # [单测用例]测试场景：协议版本常量
    def test_protocol_version_is_class_constant(self, client):
        """PROTOCOL_VERSION is 3 and accessible from instance."""
        assert BotWebSocketClient.PROTOCOL_VERSION == 3
        assert client.PROTOCOL_VERSION == 3

    # [单测用例]测试场景：URI 正确存储
    def test_uri_is_stored(self, client):
        """URI is stored as-is."""
        assert client.uri == "wss://test.example.com/ws"


class TestLoopbackWebSocketUri:
    """Loopback adapters must never inherit a workstation SOCKS proxy."""

    @pytest.mark.parametrize(
        "uri",
        [
            "ws://localhost:20017/api/openclaw/ws",
            "ws://127.0.0.1:20017/api/openclaw/ws",
            "ws://[::1]:20017/api/openclaw/ws",
        ],
    )
    def test_recognizes_loopback_targets(self, uri):
        assert _is_loopback_websocket_uri(uri) is True

    @pytest.mark.parametrize(
        "uri",
        ["wss://adapter.example.com/ws", "ws://192.0.2.1:20017/ws", "not a uri"],
    )
    def test_keeps_proxy_policy_for_non_loopback_targets(self, uri):
        assert _is_loopback_websocket_uri(uri) is False


# ==================== Tests: _next_request_id ====================


class TestNextRequestId:
    """Tests for BotWebSocketClient._next_request_id."""

    # [单测用例]测试场景：首次调用返回 "1"
    def test_starts_at_one(self, client):
        """First call returns '1'."""
        rid = client._next_request_id()
        assert rid == "1"

    # [单测用例]测试场景：顺序递增
    def test_increments_sequentially(self, client):
        """Consecutive calls return incrementing string IDs."""
        assert client._next_request_id() == "1"
        assert client._next_request_id() == "2"
        assert client._next_request_id() == "3"
        assert client._next_request_id() == "4"

    # [单测用例]测试场景：返回字符串类型
    def test_returns_string_type(self, client):
        """Returns str, not int."""
        rid = client._next_request_id()
        assert isinstance(rid, str)

    # [单测用例]测试场景：内部计数器更新
    def test_updates_internal_counter(self, client):
        """Internal _request_id int is updated."""
        client._next_request_id()
        client._next_request_id()
        assert client._request_id == 2


# ==================== Tests: connect ====================


class TestConnect:
    """Tests for BotWebSocketClient.connect."""

    # [单测用例]测试场景：连接成功并完成握手
    async def test_connect_handshake_success(self, client):
        """Successfully completes handshake with response."""
        mock_ws = AsyncMock()

        async def mock_send(data):
            sent = json.loads(data)
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": True,
                            "payload": {
                                "server": {"version": "2.5"},
                                "features": {"chat": True},
                            },
                        }
                    )

        mock_ws.send = mock_send
        mock_ws.close = AsyncMock()
        mock_ws.__aiter__ = AsyncMock(return_value=iter([]))

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with (
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.websockets.connect",
                side_effect=mock_connect,
            ) as connect,
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.is_dev",
                return_value=False,
            ),
        ):
            result = await client.connect(timeout=2.0)

        assert result["server"] == {"version": "2.5"}
        assert result["features"] == {"chat": True}
        assert client._handshake_complete is True
        assert client.connected is True
        assert client.server_info == {"version": "2.5"}
        assert client.features == {"chat": True}
        assert "proxy" not in connect.call_args.kwargs

        await client.close()

    async def test_connect_bypasses_proxy_for_loopback_adapter(self):
        """Loopback Engine WebSockets must not require python-socks."""
        client = BotWebSocketClient(uri="ws://127.0.0.1:20017/api/openclaw/ws")
        mock_ws = AsyncMock()

        async def mock_send(data):
            request_id = json.loads(data)["id"]
            future = client._pending_requests.get(request_id)
            if future is not None and not future.done():
                future.set_result(
                    {
                        "type": "res",
                        "id": request_id,
                        "ok": True,
                        "payload": {"server": {}, "features": {}},
                    }
                )

        mock_ws.send = mock_send
        mock_ws.close = AsyncMock()
        mock_ws.__aiter__ = AsyncMock(return_value=iter([]))

        with (
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.websockets.connect",
                new_callable=AsyncMock,
                return_value=mock_ws,
            ) as connect,
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.is_dev",
                return_value=False,
            ),
        ):
            await client.connect(timeout=2.0)

        assert connect.call_args.kwargs["proxy"] is None
        await client.close()

    # [单测用例]测试场景：重复连接抛出异常
    async def test_connect_raises_when_already_connected(self, client):
        """connect() raises RuntimeError if already connected."""
        client._ws = MagicMock()
        with pytest.raises(RuntimeError, match="Already connected"):
            await client.connect()

    # [单测用例]测试场景：握手失败（ok=False）
    async def test_connect_handshake_failed_ok_false(self, client):
        """connect() raises RuntimeError when handshake response has ok=False."""
        mock_ws = AsyncMock()

        async def mock_send(data):
            sent = json.loads(data)
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": False,
                            "error": {"code": 403, "message": "Forbidden"},
                        }
                    )

        mock_ws.send = mock_send
        mock_ws.close = AsyncMock()
        mock_ws.__aiter__ = AsyncMock(return_value=iter([]))

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with (
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.websockets.connect",
                side_effect=mock_connect,
            ),
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.is_dev",
                return_value=False,
            ),
        ):
            with pytest.raises(RuntimeError, match="Handshake failed"):
                await client.connect(timeout=2.0)

        await client.close()

    # [单测用例]测试场景：连接发送正确的请求帧
    async def test_connect_sends_connect_request(self, client):
        """After connection, sends a 'connect' request frame."""
        mock_ws = AsyncMock()
        sent_frames = []

        async def mock_send(data):
            sent_frames.append(json.loads(data))
            sent = json.loads(data)
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": True,
                            "payload": {"server": {}, "features": {}},
                        }
                    )

        mock_ws.send = mock_send
        mock_ws.close = AsyncMock()
        mock_ws.__aiter__ = AsyncMock(return_value=iter([]))

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with (
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.websockets.connect",
                side_effect=mock_connect,
            ),
            patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.is_dev",
                return_value=False,
            ),
        ):
            await client.connect(timeout=2.0)

        assert len(sent_frames) == 1
        frame = sent_frames[0]
        assert frame["type"] == "req"
        assert frame["method"] == "connect"
        assert frame["params"]["client"]["id"] == client.client_id
        assert frame["params"]["client"]["version"] == client.client_version

        await client.close()


# ==================== Tests: close ====================


class TestClose:
    """Tests for BotWebSocketClient.close."""

    # [单测用例]测试场景：close 关闭 ws 连接
    async def test_close_calls_ws_close(self, client):
        """close() calls _ws.close() and sets _ws to None."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True

        await client.close()

        mock_ws.close.assert_called_once()
        assert client._ws is None

    # [单测用例]测试场景：close 无 ws 时安全
    async def test_close_with_no_ws_is_safe(self, client):
        """close() does nothing if _ws is None."""
        client._ws = None
        await client.close()  # Should not raise

    # [单测用例]测试场景：close 取消 recv_task
    async def test_close_cancels_recv_task(self, client):
        """close() cancels the recv_task."""
        mock_ws = AsyncMock()
        client._ws = mock_ws

        async def never_end():
            await asyncio.sleep(9999)

        client._recv_task = asyncio.create_task(never_end())

        await client.close()

        assert client._recv_task is None
        assert client._connected is False
        assert client._handshake_complete is False

    # [单测用例]测试场景：close 重置连接标志
    async def test_close_resets_connected_flags(self, client):
        """close() resets _connected and _handshake_complete."""
        client._connected = True
        client._handshake_complete = True
        client._ws = AsyncMock()

        await client.close()

        assert client._connected is False
        assert client._handshake_complete is False


# ==================== Tests: _send_request ====================


class TestSendRequest:
    """Tests for BotWebSocketClient._send_request."""

    # [单测用例]测试场景：未连接时抛出异常
    async def test_send_request_raises_when_not_connected(self, client):
        """send_request raises RuntimeError when not connected."""
        client._connected = False
        with pytest.raises(RuntimeError, match="Not connected"):
            await client._send_request("test.method", {})

    # [单测用例]测试场景：ws 为 None 时抛出异常
    async def test_send_request_raises_when_ws_is_none(self, client):
        """send_request raises RuntimeError when _ws is None."""
        client._connected = True
        client._ws = None
        with pytest.raises(RuntimeError, match="Not connected"):
            await client._send_request("test.method", {})

    # [单测用例]测试场景：发送正确格式的请求帧
    async def test_send_request_sends_frame(self, client):
        """send_request sends a properly formatted request frame."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True

        async def mock_send(data):
            sent = json.loads(data)
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": True,
                            "payload": {"result": "ok"},
                        }
                    )

        mock_ws.send = mock_send

        result = await client._send_request("test.method", {"key": "value"})

        assert result["ok"] is True
        assert result["payload"] == {"result": "ok"}

    # [单测用例]测试场景：超时抛出 TimeoutError
    async def test_send_request_timeout(self, client):
        """send_request raises TimeoutError when response never arrives."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True

        with pytest.raises(TimeoutError, match="timed out"):
            await client._send_request("slow.method", {}, timeout=0.05)

    # [单测用例]测试场景：超时后清理 pending 请求
    async def test_send_request_cleans_up_on_timeout(self, client):
        """send_request removes pending entry on timeout."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True

        with pytest.raises(TimeoutError):
            await client._send_request("slow.method", {}, timeout=0.05)

        assert len(client._pending_requests) == 0

    # [单测用例]测试场景：每次请求 ID 唯一
    async def test_send_request_has_unique_ids(self, client):
        """Each send_request call gets a unique request_id."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True
        sent_ids = []

        async def mock_send(data):
            sent = json.loads(data)
            sent_ids.append(sent["id"])
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": True,
                            "payload": {},
                        }
                    )

        mock_ws.send = mock_send

        await client._send_request("a.b", {})
        await client._send_request("c.d", {})

        assert sent_ids[0] != sent_ids[1]
        assert len(sent_ids) == 2


# ==================== Tests: _handle_message ====================


class TestHandleMessage:
    """Tests for BotWebSocketClient._handle_message."""

    # [单测用例]测试场景：响应帧路由到 pending future
    async def test_handle_message_routes_response_to_pending_request(self, client):
        """A 'res' frame resolves the correct pending future."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client._pending_requests["5"] = future

        await client._handle_message(
            json.dumps({"type": "res", "id": "5", "ok": True, "payload": {"data": 42}})
        )

        assert future.done()
        result = future.result()
        assert result["ok"] is True
        assert result["payload"] == {"data": 42}

    # [单测用例]测试场景：未知 request_id 静默忽略
    async def test_handle_message_ignores_unknown_request_id(self, client):
        """A 'res' frame with unknown request_id is silently ignored."""
        await client._handle_message(
            json.dumps({"type": "res", "id": "nonexistent", "ok": True})
        )

    # [单测用例]测试场景：hello-ok 设置服务器信息
    async def test_handle_message_hello_ok_sets_server_info(self, client):
        """A 'hello-ok' frame sets _server_info, _features, _handshake_complete."""
        await client._handle_message(
            json.dumps(
                {
                    "type": "hello-ok",
                    "server": {"version": "2.0"},
                    "features": {"streaming": True},
                }
            )
        )

        assert client._server_info == {"version": "2.0"}
        assert client._features == {"streaming": True}
        assert client._handshake_complete is True

    # [单测用例]测试场景：事件帧调用注册的处理器
    async def test_handle_message_event_calls_registered_handler(self, client):
        """An 'event' frame calls the registered handler."""
        handler_called = []
        client._event_handlers["chat.message"] = lambda p: handler_called.append(p)

        await client._handle_message(
            json.dumps(
                {
                    "type": "event",
                    "event": "chat.message",
                    "payload": {"text": "hello"},
                }
            )
        )

        assert handler_called == [{"text": "hello"}]

    # [单测用例]测试场景：事件帧调用通配符处理器
    async def test_handle_message_event_calls_wildcard_handler(self, client):
        """An 'event' frame also calls the '*' wildcard handler."""
        wildcard_calls = []
        client._event_handlers["*"] = lambda e, p: wildcard_calls.append((e, p))

        await client._handle_message(
            json.dumps(
                {
                    "type": "event",
                    "event": "status.update",
                    "payload": {"status": "ok"},
                }
            )
        )

        assert wildcard_calls == [("status.update", {"status": "ok"})]

    # [单测用例]测试场景：事件处理器异常被捕获
    async def test_handle_message_event_handler_exception_is_logged(self, client):
        """Event handler exceptions are caught and logged, not propagated."""

        def bad_handler(payload):
            raise ValueError("handler error")

        client._event_handlers["bad.event"] = bad_handler

        # Should not raise
        await client._handle_message(
            json.dumps({"type": "event", "event": "bad.event", "payload": {}})
        )

    # [单测用例]测试场景：通配符处理器异常被捕获
    async def test_handle_message_wildcard_handler_exception_is_logged(self, client):
        """Wildcard handler exceptions are caught and logged."""

        def bad_wildcard(event_name, payload):
            raise RuntimeError("wildcard error")

        client._event_handlers["*"] = bad_wildcard

        # Should not raise
        await client._handle_message(
            json.dumps({"type": "event", "event": "some.event", "payload": {}})
        )

    # [单测用例]测试场景：无效 JSON 被捕获
    async def test_handle_message_invalid_json(self, client):
        """Invalid JSON is caught and logged, not raised."""
        await client._handle_message("not valid json {{{")

    # [单测用例]测试场景：未知帧类型被记录
    async def test_handle_message_unknown_frame_type(self, client):
        """Unknown frame types are logged as warning, not raised."""
        await client._handle_message(
            json.dumps({"type": "weird_frame", "data": "something"})
        )

    # [单测用例]测试场景：同时调用特定和通配符处理器
    async def test_both_specific_and_wildcard_handlers_called(self, client):
        """Both specific and wildcard handlers are invoked for the same event."""
        specific_calls = []
        wildcard_calls = []

        client._event_handlers["dual.event"] = lambda p: specific_calls.append(p)
        client._event_handlers["*"] = lambda e, p: wildcard_calls.append((e, p))

        await client._handle_message(
            json.dumps(
                {
                    "type": "event",
                    "event": "dual.event",
                    "payload": {"x": 1},
                }
            )
        )

        assert specific_calls == [{"x": 1}]
        assert wildcard_calls == [("dual.event", {"x": 1})]

    # [单测用例]测试场景：支持异步事件处理器
    async def test_handle_message_async_event_handler(self, client):
        """Async event handlers are properly awaited."""
        handler_called = []

        async def async_handler(payload):
            handler_called.append(payload)

        client._event_handlers["async.event"] = async_handler

        await client._handle_message(
            json.dumps(
                {
                    "type": "event",
                    "event": "async.event",
                    "payload": {"async": True},
                }
            )
        )

        assert handler_called == [{"async": True}]


# ==================== Tests: chat_send / chat_abort / session_reset ====================


class TestConvenienceMethods:
    """Tests for chat_send, chat_abort, session_reset convenience methods."""

    async def _setup_mock_ws(self, client):
        """Helper: set up a mock ws that auto-responds to requests."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True
        sent_frames = []

        async def mock_send(data):
            sent = json.loads(data)
            sent_frames.append(sent)
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": True,
                            "payload": {},
                        }
                    )

        mock_ws.send = mock_send
        return sent_frames

    # [单测用例]测试场景：chat_send 委托到 send_request
    async def test_chat_send_calls_send_request(self, client):
        """chat_send delegates to send_request with correct params."""
        sent_frames = await self._setup_mock_ws(client)

        await client.chat_send(session_key="sk-123", message="hello world")

        sent = sent_frames[0]
        assert sent["method"] == "chat.send"
        assert sent["params"]["sessionKey"] == "sk-123"
        assert sent["params"]["message"] == "hello world"
        assert sent["params"]["x-iam-token"] == "OPEN_API:NOT_PROVIDED"

    # [单测用例]测试场景：chat_send 带自定义 token 和 timeout
    async def test_chat_send_with_custom_timeout_and_token(self, client):
        """chat_send passes timeout_ms and auth_token."""
        sent_frames = await self._setup_mock_ws(client)

        await client.chat_send(
            session_key="sk-456",
            message="hello",
            timeout_ms=5000,
            auth_token="my-token",
        )

        sent = sent_frames[0]
        assert sent["params"]["timeoutMs"] == "5000"
        assert sent["params"]["x-iam-token"] == "my-token"

    # [单测用例]测试场景：chat_abort 委托到 send_request
    async def test_chat_abort_delegates_to_send_request(self, client):
        """chat_abort delegates to send_request('chat.abort', ...)."""
        sent_frames = await self._setup_mock_ws(client)

        await client.chat_abort(session_key="sk-789", run_id="run-001")

        sent = sent_frames[0]
        assert sent["method"] == "chat.abort"
        assert sent["params"]["sessionKey"] == "sk-789"
        assert sent["params"]["runId"] == "run-001"

    # [单测用例]测试场景：chat_abort 无 run_id
    async def test_chat_abort_without_run_id(self, client):
        """chat_abort without run_id omits the field."""
        sent_frames = await self._setup_mock_ws(client)

        await client.chat_abort(session_key="sk-000")

        sent = sent_frames[0]
        assert "runId" not in sent["params"]

    # [单测用例]测试场景：session_reset 委托到 send_request
    async def test_session_reset_delegates_to_send_request(self, client):
        """session_reset delegates to send_request('sessions.reset', ...)."""
        sent_frames = await self._setup_mock_ws(client)

        await client.session_reset(session_key="sk-reset")

        sent = sent_frames[0]
        assert sent["method"] == "sessions.reset"
        assert sent["params"] == {"sessionKey": "sk-reset"}


# ==================== Tests: ChatRequestError ====================


class TestChatRequestError:
    """Tests for ChatRequestError ok=False handling in chat_send/chat_inject."""

    async def _setup_mock_ws_ok_false(self, client, error_payload):
        """Helper: set up a mock ws that responds with ok=False."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True

        async def mock_send(data):
            sent = json.loads(data)
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": False,
                            "error": error_payload,
                        }
                    )

        mock_ws.send = mock_send

    # [单测用例]测试场景：chat_send ok=False 抛出 ChatRequestError
    async def test_chat_send_ok_false_raises_error(self, client):
        """chat_send raises ChatRequestError when response ok=False."""
        error_payload = {
            "code": "UNAVAILABLE",
            "message": "Session validation failed",
            "retryable": True,
        }
        await self._setup_mock_ws_ok_false(client, error_payload)

        with pytest.raises(ChatRequestError, match="chat.send failed"):
            await client.chat_send(session_key="sk-err", message="hello")

    # [单测用例]测试场景：chat_send ChatRequestError 包含 error 详情
    async def test_chat_send_error_details(self, client):
        """ChatRequestError from chat_send includes error_code, error_message, retryable."""
        error_payload = {
            "code": "UNAVAILABLE",
            "message": "Session validation failed",
            "retryable": True,
        }
        await self._setup_mock_ws_ok_false(client, error_payload)

        with pytest.raises(ChatRequestError) as exc_info:
            await client.chat_send(session_key="sk-err", message="hello")

        err = exc_info.value
        assert err.error_code == "UNAVAILABLE"
        assert err.error_message == "Session validation failed"
        assert err.retryable is True

    # [单测用例]测试场景：chat_send ok=False 缺少 error 字段时安全处理
    async def test_chat_send_ok_false_missing_error_fields(self, client):
        """ChatRequestError handles missing error fields gracefully."""
        await self._setup_mock_ws_ok_false(client, {})

        with pytest.raises(ChatRequestError) as exc_info:
            await client.chat_send(session_key="sk-err", message="hello")

        err = exc_info.value
        assert err.error_code is None
        assert err.error_message is None
        assert err.retryable is None

    # [单测用例]测试场景：chat_inject ok=False 抛出 ChatRequestError
    async def test_chat_inject_ok_false_raises_error(self, client):
        """chat_inject raises ChatRequestError when response ok=False."""
        error_payload = {
            "code": "FORBIDDEN",
            "message": "Permission denied",
            "retryable": False,
        }
        await self._setup_mock_ws_ok_false(client, error_payload)

        with pytest.raises(ChatRequestError, match="chat.inject failed"):
            await client.chat_inject(session_key="sk-err", message="inject")

    # [单测用例]测试场景：chat_inject ChatRequestError 包含 error 详情
    async def test_chat_inject_error_details(self, client):
        """ChatRequestError from chat_inject includes error_code, error_message, retryable."""
        error_payload = {
            "code": "FORBIDDEN",
            "message": "Permission denied",
            "retryable": False,
        }
        await self._setup_mock_ws_ok_false(client, error_payload)

        with pytest.raises(ChatRequestError) as exc_info:
            await client.chat_inject(session_key="sk-err", message="inject")

        err = exc_info.value
        assert err.error_code == "FORBIDDEN"
        assert err.error_message == "Permission denied"
        assert err.retryable is False

    # [单测用例]测试场景：chat_send ok=True 正常返回
    async def test_chat_send_ok_true_returns_result(self, client):
        """chat_send returns result when ok=True."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        client._connected = True

        async def mock_send(data):
            sent = json.loads(data)
            req_id = sent["id"]
            entry = client._pending_requests.get(req_id)
            if entry:
                if not entry.done():
                    entry.set_result(
                        {
                            "type": "res",
                            "id": req_id,
                            "ok": True,
                            "payload": {"accepted": True},
                        }
                    )

        mock_ws.send = mock_send

        result = await client.chat_send(session_key="sk-ok", message="hello")
        assert result["ok"] is True
        assert result["payload"]["accepted"] is True


# ==================== Tests: on_event ====================


class TestOnEvent:
    """Tests for BotWebSocketClient.on_event."""

    # [单测用例]测试场景：注册事件处理器
    def test_on_event_registers_handler(self, client):
        """on_event registers an event handler in _event_handlers."""
        handler = lambda p: None
        client.on_event("custom.event", handler)
        assert client._event_handlers["custom.event"] is handler

    # [单测用例]测试场景：覆盖之前的处理器
    def test_on_event_overwrites_previous_handler(self, client):
        """on_event overwrites a previously registered handler."""
        h1 = lambda p: "first"
        h2 = lambda p: "second"
        client.on_event("my.event", h1)
        client.on_event("my.event", h2)
        assert client._event_handlers["my.event"] is h2


# ==================== Tests: _get_default_headers ====================


class TestGetDefaultHeaders:
    """Tests for BotWebSocketClient._get_default_headers."""

    # [单测用例]测试场景：默认 headers 包含 User-Agent
    def test_default_headers_includes_user_agent(self, client):
        """_get_default_headers includes User-Agent."""
        headers = client._get_default_headers()
        assert headers["User-Agent"] == "OpenClaw-Python-Client/1.0"

    # [单测用例]测试场景：自定义 headers 合并覆盖
    def test_default_headers_merges_custom_headers(self, client):
        """Custom headers are merged on top of defaults."""
        client.headers = {"X-Custom": "value", "User-Agent": "custom-agent"}
        headers = client._get_default_headers()
        assert headers["X-Custom"] == "value"
        assert headers["User-Agent"] == "custom-agent"
