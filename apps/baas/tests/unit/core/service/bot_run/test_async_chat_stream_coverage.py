"""test async chat stream coverage; focused regression tests and fixtures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_run._async_chat_client import (
    ConcurrentSessionError,
    NotConnectedError,
)
from tests.utils.chat_transport import make_chat_client

from ._async_chat_coverage_support import (
    _setup_session_state,
)
from ._async_chat_coverage_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_coverage_support import (
    mock_bot_ws_instance as mock_bot_ws_instance,
)


class TestOnDisconnect:
    def test_on_disconnect_pushes_error_to_streams(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        state1 = _setup_session_state(client, "s1")
        state1.stream_queue = asyncio.Queue()
        state2 = _setup_session_state(client, "s2")
        state2.stream_queue = asyncio.Queue()

        # State without queue should not cause error
        state3 = _setup_session_state(client, "s3")

        client._on_disconnect("disconnect", {"reason": "closed"})

        chunk1 = state1.stream_queue.get_nowait()
        assert chunk1.type == "error"
        assert chunk1.content == "connection lost"
        chunk2 = state2.stream_queue.get_nowait()
        assert chunk2.type == "error"
        # 断连时所有在途会话被包装成终态 error（无 queue 的会话仅置状态）
        for state in (state1, state2, state3):
            assert state.state == "error"
            assert state.chat_complete.is_set()

    def test_on_disconnect_skips_completed_sessions(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        done = _setup_session_state(client, "done")
        done.state = "final"
        done.content = "ok"
        done.chat_complete.set()
        done.stream_queue = asyncio.Queue()

        client._on_disconnect("disconnect", {})

        # 已完成的会话不被覆盖成 error，也不追加 error chunk
        assert done.state == "final"
        assert done.stream_queue.empty()

    def test_on_disconnect_sets_disconnect_event(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        assert client._disconnect_event.is_set() is False
        client._on_disconnect("disconnect", {})
        assert client._disconnect_event.is_set() is True

    def test_notify_disconnect(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        assert client._disconnect_event.is_set() is False
        client._notify_disconnect()
        assert client._disconnect_event.is_set() is True


class TestSendMessageStream:
    @pytest.mark.asyncio
    async def test_stream_success(self, mock_bot_ws, mock_bot_ws_instance):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def push_chunks(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(
                    StreamChunk(type="delta", content="chunk1")
                )
                state.stream_queue.put_nowait(StreamChunk(type="final", content="done"))
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = push_chunks

        chunks = []
        async for chunk in client.send_message_stream("hello", session_key="sk1"):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].type == "delta"
        assert chunks[1].type == "final"

    @pytest.mark.asyncio
    async def test_stream_not_connected_raises(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        with pytest.raises(NotConnectedError):
            async for _ in client.send_message_stream("hello"):
                pass

    @pytest.mark.asyncio
    async def test_stream_error_chunk_terminates(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def push_error(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(StreamChunk(type="error", content="bad"))
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = push_error

        chunks = []
        async for chunk in client.send_message_stream("hello", session_key="sk1"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_stream_with_timeout(self, mock_bot_ws, mock_bot_ws_instance):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def push_chunks(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(StreamChunk(type="delta", content="d1"))
                state.stream_queue.put_nowait(StreamChunk(type="final", content="end"))
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = push_chunks

        chunks = []
        async for chunk in client.send_message_stream(
            "hi", session_key="sk1", timeout=5
        ):
            chunks.append(chunk)

        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_stream_timeout_exceeded(self, mock_bot_ws, mock_bot_ws_instance):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def never_push(*args, **kwargs):
            return {"ok": True}  # Don't push any chunks

        mock_bot_ws_instance.chat_send.side_effect = never_push

        chunks = []
        async for chunk in client.send_message_stream(
            "hi", session_key="sk1", timeout=0.1
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "error"
        assert "timeout" in chunks[0].content

    @pytest.mark.asyncio
    async def test_stream_with_semaphore(self, mock_bot_ws, mock_bot_ws_instance):
        client = make_chat_client(
            uri="ws://host/ws", max_concurrent_sessions=1, max_retries=0
        )
        await client.connect()

        async def push_final(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(StreamChunk(type="final", content="ok"))
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = push_final

        chunks = []
        async for chunk in client.send_message_stream("hi", session_key="sk1"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "final"

    @pytest.mark.asyncio
    async def test_stream_concurrent_same_key_timeout(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(
            uri="ws://host/ws", session_key_timeout=0.05, max_retries=0
        )
        await client.connect()

        async def never_push(*args, **kwargs):
            await asyncio.sleep(10)
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = never_push

        t1 = asyncio.create_task(
            client.send_message_stream("m1", session_key="same").__anext__()
        )
        await asyncio.sleep(0.01)

        with pytest.raises(ConcurrentSessionError):
            async for _ in client.send_message_stream("m2", session_key="same"):
                pass

        t1.cancel()
        try:
            await t1
        except (asyncio.CancelledError, StopAsyncIteration, Exception):
            pass

    @pytest.mark.asyncio
    async def test_stream_connection_lost_before_send(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()

        # Simulate connection lost between condition release and send
        mock_bot_ws_instance.connected = False

        with pytest.raises(NotConnectedError):
            async for _ in client.send_message_stream("hi", session_key="sk1"):
                pass


class TestDrainStreamQueue:
    @pytest.mark.asyncio
    async def test_drain_no_timeout(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        queue.put_nowait(StreamChunk(type="delta", content="d1"))
        queue.put_nowait(StreamChunk(type="final", content="f1"))

        chunks = []
        async for chunk in client._drain_stream_queue(queue, None):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[1].type == "final"

    @pytest.mark.asyncio
    async def test_drain_timeout_remaining_zero(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        # Empty queue with very short timeout → immediate timeout
        chunks = []
        async for chunk in client._drain_stream_queue(queue, 0.01):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_drain_timeout_after_some_chunks(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        queue.put_nowait(StreamChunk(type="delta", content="d1"))

        chunks = []
        async for chunk in client._drain_stream_queue(queue, 0.05):
            chunks.append(chunk)

        # First chunk delivered, then timeout on empty queue
        assert len(chunks) == 2
        assert chunks[0].type == "delta"
        assert chunks[1].type == "error"


class TestInjectMessageAdditional:
    @pytest.mark.asyncio
    async def test_inject_auto_generates_session_key(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()
        await client.inject_message("hello")
        mock_bot_ws_instance.chat_inject.assert_called_once()
        sk = mock_bot_ws_instance.chat_inject.call_args[1]["session_key"]
        assert sk is not None
        assert len(sk) > 0

    @pytest.mark.asyncio
    async def test_inject_with_chat_metadata(self, mock_bot_ws, mock_bot_ws_instance):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()
        await client.inject_message(
            "hello", session_key="sk1", chat_metadata={"key": "value"}
        )
        mock_bot_ws_instance.chat_inject.assert_called_once()
        assert mock_bot_ws_instance.chat_inject.call_args[1]["chat_metadata"] == {
            "key": "value"
        }


class TestSendMessageAdditional:
    @pytest.mark.asyncio
    async def test_send_message_connection_lost_before_send(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()

        # is_connected is True at entry, then False at the second check (line 352)
        call_count = 0
        original_connected = mock_bot_ws_instance.connected

        class ConnectedProp:
            def __bool__(self):
                nonlocal call_count
                call_count += 1
                return call_count == 1  # True first time, False after

        mock_bot_ws_instance.connected = ConnectedProp()

        with pytest.raises(NotConnectedError, match="Connection lost"):
            await client.send_message("hi", session_key="sk1")

    @pytest.mark.asyncio
    async def test_send_message_with_app_id_and_metadata(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def fire_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = fire_complete

        await client.send_message(
            "hi",
            session_key="sk1",
            app_id="my-app",
            chat_metadata={"k": "v"},
            timeout=5,
        )
        call_kwargs = mock_bot_ws_instance.chat_send.call_args[1]
        assert call_kwargs["app_id"] == "my-app"
        assert call_kwargs["chat_metadata"] == {"k": "v"}
        assert call_kwargs["timeout_ms"] == 5000

    @pytest.mark.asyncio
    async def test_send_message_with_semaphore(self, mock_bot_ws, mock_bot_ws_instance):
        client = make_chat_client(
            uri="ws://host/ws", max_concurrent_sessions=1, max_retries=0
        )
        await client.connect()

        async def fire_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = fire_complete

        content, _ = await client.send_message("hi", session_key="sk1")
        assert content == ""


class TestReconnectLoop:
    @pytest.mark.asyncio
    async def test_reconnect_success(self, mock_bot_ws, mock_bot_ws_instance):
        """Test that reconnect loop reconnects after disconnect."""
        client = make_chat_client(
            uri="ws://host/ws", max_retries=2, retry_base_backoff=0.01
        )
        await client.connect()

        # Simulate disconnect
        mock_bot_ws_instance.connected = False
        client._notify_disconnect()

        # Wait for reconnect to happen
        await asyncio.sleep(0.1)

        # The second BotWebSocketClient instance should have been created
        assert mock_bot_ws.call_count >= 2
        assert (
            mock_bot_ws_instance.on_event.call_args_list.count(
                call(
                    "mode_transition.resolved",
                    client._on_mode_transition_resolved,
                )
            )
            >= 2
        )

        await client.close()

    @pytest.mark.asyncio
    async def test_reconnect_all_attempts_exhausted(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(
            uri="ws://host/ws", max_retries=1, retry_base_backoff=0.01
        )
        await client.connect()

        # Make subsequent connect calls fail
        mock_bot_ws_instance.connected = False
        mock_bot_ws_instance.connect = AsyncMock(side_effect=ConnectionError("fail"))
        client._notify_disconnect()

        # Wait for reconnect attempts to exhaust
        await asyncio.sleep(0.1)

        assert client._reconnecting is False
        await client.close()

    @pytest.mark.asyncio
    async def test_reconnect_loop_stops_on_close(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(
            uri="ws://host/ws", max_retries=3, retry_base_backoff=0.01
        )
        await client.connect()
        monitor = client._reconnect_monitor
        assert monitor is not None

        await client.close()
        assert monitor.cancelled() or monitor.done()

    @pytest.mark.asyncio
    async def test_reconnect_old_client_close_exception(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """When closing old client during reconnect fails, it should be swallowed."""
        client = make_chat_client(
            uri="ws://host/ws", max_retries=1, retry_base_backoff=0.01
        )
        await client.connect()

        # First instance's close raises, but reconnect should still succeed
        # with a new instance that closes normally
        first_instance = mock_bot_ws_instance
        first_instance.close = AsyncMock(side_effect=Exception("close fail"))
        first_instance.connected = False

        # Second instance (created during reconnect) works fine
        second_instance = MagicMock()
        second_instance.connect = AsyncMock(
            return_value={"server": {"host": "srv2"}, "features": {}}
        )
        second_instance.close = AsyncMock()
        second_instance.connected = True
        second_instance.chat_send = AsyncMock()
        second_instance.chat_inject = AsyncMock()
        mock_bot_ws.return_value = second_instance

        client._notify_disconnect()
        await asyncio.sleep(0.05)

        await client.close()

    @pytest.mark.asyncio
    async def test_reconnect_closed_intentionally_during_backoff(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(
            uri="ws://host/ws", max_retries=3, retry_base_backoff=0.05
        )
        await client.connect()

        mock_bot_ws_instance.connected = False
        client._notify_disconnect()

        # Close during backoff sleep
        await asyncio.sleep(0.01)
        await client.close()


class TestCloseAdditional:
    @pytest.mark.asyncio
    async def test_close_with_reconnect_monitor_done(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()
        # No reconnect monitor when max_retries=0
        assert client._reconnect_monitor is None
        await client.close()
        assert client._closed_intentionally is True

    @pytest.mark.asyncio
    async def test_close_cancels_monitor_await_cancelled(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=1)
        await client.connect()
        monitor = client._reconnect_monitor
        assert monitor is not None

        await client.close()
        assert monitor.done() or monitor.cancelled()
        assert client._reconnect_monitor is None

    @pytest.mark.asyncio
    async def test_close_when_already_closed(self, mock_bot_ws, mock_bot_ws_instance):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()
        await client.close()
        # Double close should not raise
        await client.close()
