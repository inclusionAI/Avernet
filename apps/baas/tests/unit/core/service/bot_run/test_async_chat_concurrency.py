"""test async chat concurrency; focused regression tests and fixtures."""

import asyncio

import pytest

from tests.utils.chat_transport import make_chat_client

from ._async_chat_test_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_test_support import (
    mock_bot_ws_instance as mock_bot_ws_instance,
)


class TestConcurrencySemaphore:
    @pytest.mark.asyncio
    async def test_semaphore_limits_parallel(self, mock_bot_ws, mock_bot_ws_instance):
        """max_concurrent_sessions=2 时，第 3 个请求会阻塞直到前面的完成。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", max_concurrent_sessions=2)
        await client.connect()

        running = 0
        max_running = 0
        lock = asyncio.Lock()

        async def slow_chat_send(*args, **kwargs):
            nonlocal running, max_running
            async with lock:
                running += 1
                max_running = max(max_running, running)
            await asyncio.sleep(0.05)
            async with lock:
                running -= 1
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = slow_chat_send

        tasks = [
            asyncio.create_task(client.send_message(f"msg{i}", session_key=f"sess-{i}"))
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 3
        # 信号量限制为 2，所以同时运行的最大数不超过 2
        assert max_running <= 2

    @pytest.mark.asyncio
    async def test_semaphore_unlimited(self, mock_bot_ws, mock_bot_ws_instance):
        """max_concurrent_sessions=0 时，所有并发请求立即执行。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", max_concurrent_sessions=0)
        await client.connect()

        async def instant_chat_send(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = instant_chat_send

        tasks = [
            asyncio.create_task(client.send_message(f"msg{i}", session_key=f"sess-{i}"))
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 5


class TestSessionKeyQueueing:
    @pytest.mark.asyncio
    async def test_same_key_queues_instead_of_reject(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """同一 sessionKey 的第二个请求排队等待第一个完成后执行，而非硬拒绝。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", session_key_timeout=5.0)
        await client.connect()

        order = []

        async def first_send(*args, **kwargs):
            order.append("first_start")
            await asyncio.sleep(0.1)
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.content = "first response"
                state.chat_complete.set()
            order.append("first_end")
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = first_send

        # 第一个发送
        t1 = asyncio.create_task(client.send_message("first", session_key="same-key"))
        await asyncio.sleep(0.02)  # 让 t1 开始

        # 第二个发送 —— 应该排队等待 t1 完成
        # 使用一个 side_effect 函数，在 t1 完成后设置 chat_complete
        async def second_send(*args, **kwargs):
            order.append("second_start")
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.content = "second response"
                state.chat_complete.set()
            order.append("second_end")
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = second_send

        t2 = asyncio.create_task(client.send_message("second", session_key="same-key"))

        r1 = await t1
        r2 = await t2

        assert r1[0] == "first response"
        assert r2[0] == "second response"
        # 确保顺序正确：第一个完成后第二个才开始
        assert order.index("first_start") < order.index("first_end")
        # second_start 应在 first_end 之后
        assert order.index("second_start") > order.index("first_end")

    @pytest.mark.asyncio
    async def test_same_key_queueing_timeout(self, mock_bot_ws, mock_bot_ws_instance):
        """排队等待超时后抛 ConcurrentSessionError。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            ConcurrentSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", session_key_timeout=0.05)
        await client.connect()

        async def never_complete(*args, **kwargs):
            await asyncio.sleep(10)
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = never_complete

        t1 = asyncio.create_task(client.send_message("msg", session_key="same-key"))
        await asyncio.sleep(0.01)

        with pytest.raises(ConcurrentSessionError, match="Timed out waiting"):
            await client.send_message("msg2", session_key="same-key")

        t1.cancel()
        try:
            await t1
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_different_keys_no_blocking(self, mock_bot_ws, mock_bot_ws_instance):
        """不同 sessionKey 的请求互不阻塞。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        async def instant_send(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = instant_send

        results = await asyncio.gather(
            client.send_message("msg1", session_key="key-1"),
            client.send_message("msg2", session_key="key-2"),
        )
        assert len(results) == 2


class TestReconnection:
    @pytest.mark.asyncio
    async def test_is_reconnecting_property(self, mock_bot_ws, mock_bot_ws_instance):
        """is_reconnecting 初始为 False。"""

        client = make_chat_client(uri="ws://host/ws")
        assert client.is_reconnecting is False

    @pytest.mark.asyncio
    async def test_max_retries_zero_disables_reconnect(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """max_retries=0 时不启动重连监控任务。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()
        # max_retries=0 时不启动 _reconnect_monitor
        assert client._reconnect_monitor is None

    @pytest.mark.asyncio
    async def test_max_retries_nonzero_starts_reconnect_monitor(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """max_retries>0 时启动重连监控任务。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", max_retries=1)
        await client.connect()
        assert client._reconnect_monitor is not None
        assert not client._reconnect_monitor.done()

        await client.close()

    @pytest.mark.asyncio
    async def test_close_sets_intentionally_closed(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """close() 设置 _closed_intentionally=True 并取消重连监控。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", max_retries=2)
        await client.connect()
        monitor = client._reconnect_monitor
        assert monitor is not None

        await client.close()
        assert client._closed_intentionally is True
        assert monitor.cancelled() or monitor.done()


class TestNewInitParams:
    def test_default_concurrency_params(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        assert client._max_concurrent_sessions == 0
        assert client._session_key_timeout == 30.0
        assert client._max_retries == 1
        assert client._retry_base_backoff == 0.5
        assert client._concurrency_sem is None  # 0 = 无限制
        assert client._active_sessions == set()

    def test_custom_concurrency_params(self, mock_bot_ws):

        client = make_chat_client(
            uri="ws://host/ws",
            max_concurrent_sessions=5,
            session_key_timeout=10.0,
            max_retries=3,
            retry_base_backoff=1.0,
        )
        assert client._max_concurrent_sessions == 5
        assert client._session_key_timeout == 10.0
        assert client._max_retries == 3
        assert client._retry_base_backoff == 1.0
        assert client._concurrency_sem is not None
        assert client._concurrency_sem._value == 5

    def test_active_sessions_is_set(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        assert isinstance(client._active_sessions, set)

    @pytest.mark.asyncio
    async def test_close_clears_active_sessions(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """关闭时清理所有 active sessions。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        # 手动添加 active sessions
        client._active_sessions.add("s1")
        client._active_sessions.add("s2")

        await client.close()
        assert client._active_sessions == set()


class TestNotConnectedError:
    @pytest.mark.asyncio
    async def test_send_message_raises_not_connected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message raises NotConnectedError when not connected."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            NotConnectedError,
        )

        client = make_chat_client(uri="ws://host/ws")
        # _client is None → NotConnectedError
        with pytest.raises(NotConnectedError, match="Not connected"):
            await client.send_message("Hi", session_key="s1")

    @pytest.mark.asyncio
    async def test_inject_message_raises_not_connected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """inject_message raises NotConnectedError when not connected."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            NotConnectedError,
        )

        client = make_chat_client(uri="ws://host/ws")
        with pytest.raises(NotConnectedError, match="Not connected"):
            await client.inject_message("Hi", session_key="s1")


class TestInjectMessageSemaphore:
    @pytest.mark.asyncio
    async def test_inject_respects_semaphore(self, mock_bot_ws, mock_bot_ws_instance):
        """inject_message 受并发信号量约束。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", max_concurrent_sessions=1)
        await client.connect()

        barrier = asyncio.Event()
        in_inject = asyncio.Event()

        async def slow_inject(*args, **kwargs):
            in_inject.set()
            await barrier.wait()
            return {"ok": True}

        mock_bot_ws_instance.chat_inject.side_effect = slow_inject

        # 第一个 inject 占用信号量
        t1 = asyncio.create_task(client.inject_message("m1", session_key="s1"))
        await asyncio.sleep(0.02)
        assert in_inject.is_set()

        # 第二个 inject 应该被信号量阻塞
        started = asyncio.Event()

        async def inject_with_signal(*args, **kwargs):
            started.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_inject.side_effect = inject_with_signal

        t2 = asyncio.create_task(client.inject_message("m2", session_key="s2"))
        # 短暂等待，t2 不应该开始（信号量被 t1 占用）
        await asyncio.sleep(0.02)
        assert not started.is_set()

        # 释放 t1
        barrier.set()
        await t1
        # t2 现在应该可以运行
        await t2
        assert started.is_set()

    @pytest.mark.asyncio
    async def test_inject_no_semaphore_when_zero(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """max_concurrent_sessions=0 时 inject_message 不受信号量限制。"""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws", max_concurrent_sessions=0)
        await client.connect()

        call_count = 0

        async def count_inject(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"ok": True}

        mock_bot_ws_instance.chat_inject.side_effect = count_inject

        # 多个并发 inject 都应该立即执行
        await asyncio.gather(
            client.inject_message("m1", session_key="s1"),
            client.inject_message("m2", session_key="s2"),
            client.inject_message("m3", session_key="s3"),
        )
        assert call_count == 3
