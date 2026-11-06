"""test async chat a2a fallback; focused regression tests and fixtures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    BotSessionError,
)
from tests.utils.chat_transport import make_chat_client

from ._async_chat_a2a_test_support import (
    _a2a_acc_with_stub_main_client,
    _GatedV3Transport,
    _StubV3Transport,
)
from ._async_chat_a2a_test_support import (
    stub_v3_patch as stub_v3_patch,
)
from ._async_chat_coverage_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_coverage_support import (
    mock_bot_ws_instance as mock_bot_ws_instance,
)


class TestInjectAttachmentsV3Reroute:
    """16-5 双入口的另一侧：注入带附件同样必须重路由到 v3 专用连接。"""

    @pytest.mark.asyncio
    async def test_a2a_inject_with_attachments_reroutes_to_v3(
        self, mock_bot_ws, stub_v3_patch
    ):
        acc = _a2a_acc_with_stub_main_client()

        await acc.inject_message(
            "with files", session_key="sk-att", attachments=[{"kind": "screenshot"}]
        )

        fallback = acc._chat_transport.fallback_client
        assert fallback is not None, "附件注入必须懒建 v3 专用连接"
        assert fallback is _StubV3Transport.instances[-1]
        assert fallback.inject_calls, "chat_inject 未打到 v3 fallback"
        session_key, args, kwargs = fallback.inject_calls[0]
        assert session_key == "sk-att"
        assert args == (), f"参数应全关键字转 v3: {args}"
        assert kwargs["message"] == "with files", f"message 必须原样转 v3: {kwargs}"
        assert kwargs["attachments"] == [{"kind": "screenshot"}]
        # 注入不开启主 run 等待面；保留旧行为：不注入归属表。
        assert acc._chat_transport.routed_sessions == {}

        await acc.close()
        assert acc._chat_transport.fallback_client is None, (
            "注入收口后 fallback 随 close 清空"
        )

    @pytest.mark.asyncio
    async def test_second_a2a_inject_reuses_cached_v3_fallback(
        self, mock_bot_ws, stub_v3_patch
    ):
        """fallback 懒建一次后必须缓存复用——不得逐请求重建连接。"""
        acc = _a2a_acc_with_stub_main_client()

        await acc.inject_message("first", session_key="sk-1", attachments=[{"k": 1}])
        first_fallback = acc._chat_transport.fallback_client
        await acc.inject_message("second", session_key="sk-2", attachments=[{"k": 2}])

        assert acc._chat_transport.fallback_client is first_fallback, (
            "second 注入必须复用同一 fallback"
        )
        created = len(_StubV3Transport.instances)
        assert created == 1, f"fallback 被重建 {created} 次（应为 1）"
        assert len(first_fallback.inject_calls) == 2

        await acc.close()
        assert first_fallback.close_calls == 1, "收口必须关唯一的 fallback"

    @pytest.mark.asyncio
    async def test_a2a_inject_attachment_preserves_send_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """注入附件走 fallback，但不覆盖/清空已有主 run 归属。"""
        acc = _a2a_acc_with_stub_main_client()

        await acc.send_message(
            "main", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )
        assert "sk-att" in acc._chat_transport.routed_sessions

        await acc.inject_message("side", session_key="sk-att", attachments=[{"k": 2}])

        assert "sk-att" in acc._chat_transport.routed_sessions
        await acc.close()

    async def test_bridge_snapshot_is_available_before_connect(self, mock_bot_ws):
        """回调表不依赖低层客户端的私有 registry，握手前即可绑定。"""
        acc = make_chat_client(uri="ws://host/ws", max_retries=0)
        assert acc._event_bridge_snapshot()["chat"] == acc._on_chat
        assert set(acc._event_bridge_snapshot()) == {
            "chat",
            "agent",
            "interaction.requested",
            "interaction.resolved",
            "mode_transition.resolved",
            "error",
            "*",
        }

    @pytest.mark.asyncio
    async def test_close_swallows_v3_fallback_close_failure(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """fallback close 抛错必须吞掉——主连接收口不得被旁支拖死。"""
        acc = make_chat_client(uri="ws://host/ws", max_retries=0, transport_mode="grey")
        await acc.connect()

        bad_fallback = MagicMock()
        bad_fallback.close = AsyncMock(side_effect=RuntimeError("fallback boom"))
        acc._chat_transport.fallback_client = bad_fallback

        await acc.close()  # 不得上抛

        bad_fallback.close.assert_awaited_once()
        assert acc._chat_transport.fallback_client is None, "close 后 fallback 必须清空"
        mock_bot_ws_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_cleans_normal_v3_fallback(
        self, mock_bot_ws, mock_bot_ws_instance, stub_v3_patch
    ):
        acc = _a2a_acc_with_stub_main_client()

        await acc.inject_message("m", session_key="sk-1", attachments=[{"k": 1}])
        fallback = acc._chat_transport.fallback_client
        assert fallback is not None

        await acc.close()

        assert fallback.close_calls == 1, "fallback 随 close 收口"
        assert acc._chat_transport.fallback_client is None
        assert acc._chat_transport.routed_sessions == {}


class TestV3FallbackDisconnectRecovery:
    """PR #2743 R3-2：fallback 断连的隔离收口与连接恢复。

    回归钉：fallback WS 断连此前既不通知等待方（ACK 后上层悬挂等自身
    超时）、也不失效坏连接（后续附件请求继续复用）。现在断连处理器只
    终结归属 fallback 的会话——A2A 主连接的在途请求不受影响。
    """

    async def _wait_until(self, predicate, timeout_s: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("condition not reached in time")
            await asyncio.sleep(0.005)

    def _acc_with_awaitable_main(self) -> AsyncChatClient:
        acc = _a2a_acc_with_stub_main_client()
        acc._chat_transport._client.chat_send = AsyncMock(return_value={"ok": True})
        return acc

    @pytest.mark.asyncio
    async def test_ack_then_disconnect_terminates_waiter(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 1+2 组合（非流式）：ACK 后断连——无超时等待方及时收到
        明确 error，不悬挂；且不自动重发已 ACK 的请求。"""
        acc = self._acc_with_awaitable_main()

        task = asyncio.create_task(
            acc.send_message(
                "hi",
                session_key="sk-att-1",
                attachments=[{"kind": "screenshot"}],
            )
        )
        await self._wait_until(
            lambda: (
                bool(_StubV3Transport.instances)
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        fallback = acc._chat_transport.fallback_client
        assert fallback is not None

        fallback.simulate_disconnect()

        with pytest.raises(BotSessionError, match="fallback connection lost"):
            await asyncio.wait_for(task, 2)
        # 不自动重发：断连后 fallback/主连接都没有第二次 chat.send
        assert len(fallback.chat_send_calls) == 1
        assert acc._chat_transport._client.chat_send.await_count == 0
        # 坏连接不再被持有；归属路由表收口
        assert acc._chat_transport.fallback_client is None
        assert acc._chat_transport.routed_sessions == {}

    @pytest.mark.asyncio
    async def test_ack_then_disconnect_terminates_stream_waiter(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 2（流式）：断连后流式消费者收到 error chunk 并结束迭代。"""
        acc = self._acc_with_awaitable_main()

        async def consume() -> list[StreamChunk]:
            chunks: list[StreamChunk] = []
            async for chunk in acc.send_message_stream(
                "hi",
                session_key="sk-att-2",
                attachments=[{"kind": "screenshot"}],
            ):
                chunks.append(chunk)
            return chunks

        task = asyncio.create_task(consume())
        await self._wait_until(
            lambda: (
                bool(_StubV3Transport.instances)
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        acc._chat_transport.fallback_client.simulate_disconnect()

        chunks = await asyncio.wait_for(task, 2)
        errs = [c for c in chunks if c.type == "error"]
        assert errs, f"stream ended without error chunk: {chunks}"
        assert "fallback connection lost" in errs[-1].content

    @pytest.mark.asyncio
    async def test_disconnect_spares_inflight_a2a_requests(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 3：fallback 断连只终结归属会话——A2A 主连接上 ACK 已过、
        仍在等回复的请求不被误杀（R17-5 隔离裁决）。"""
        acc = self._acc_with_awaitable_main()

        a2a_task = asyncio.create_task(acc.send_message("plain", session_key="sk-a2a"))
        att_task = asyncio.create_task(
            acc.send_message(
                "hi",
                session_key="sk-att-3",
                attachments=[{"kind": "screenshot"}],
            )
        )
        await self._wait_until(
            lambda: (
                bool(_StubV3Transport.instances)
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        await self._wait_until(
            lambda: acc._chat_transport._client.chat_send.await_count == 1
        )

        acc._chat_transport.fallback_client.simulate_disconnect()

        with pytest.raises(BotSessionError):
            await asyncio.wait_for(att_task, 2)
        # A2A 请求不受影响：仍在等待
        await asyncio.sleep(0.1)
        assert not a2a_task.done(), "fallback 断连误杀了 A2A 侧在途请求"
        # 收尾：A2A 会话正常自然结束（chat_complete 置位）
        state = acc._sessions.get("sk-a2a")
        state.chat_complete.set()
        await asyncio.wait_for(a2a_task, 2)

    @pytest.mark.asyncio
    async def test_attachment_after_disconnect_rebuilds_connection(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 4：断连后再次发送附件——建立新连接并正常发送，不缺
        不重用旧连接。"""
        acc = self._acc_with_awaitable_main()

        async def send_att(sk: str, min_count: int):
            task = asyncio.create_task(
                acc.send_message(
                    "hi", session_key=sk, attachments=[{"kind": "screenshot"}]
                )
            )
            await self._wait_until(
                lambda: (
                    bool(_StubV3Transport.instances)
                    and len(_StubV3Transport.instances[-1].chat_send_calls) >= min_count
                )
            )
            return task

        task1 = await send_att("sk-att-4a", 1)
        first = acc._chat_transport.fallback_client
        first.simulate_disconnect()
        with pytest.raises(BotSessionError):
            await asyncio.wait_for(task1, 2)

        created = len(_StubV3Transport.instances)
        task2 = asyncio.create_task(
            acc.send_message(
                "hi", session_key="sk-att-4b", attachments=[{"kind": "screenshot"}]
            )
        )
        await self._wait_until(
            lambda: (
                len(_StubV3Transport.instances) > created
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        second = acc._chat_transport.fallback_client
        assert second is not None and second is not first, "复用了已断开连接"
        assert len(_StubV3Transport.instances) == created + 1
        assert second in _StubV3Transport.instances
        assert len(second.chat_send_calls) == 1

        # 收尾：第二连接存活；旧连接的迟到断连通知（验收 5）不
        # 清新连接、不误杀其等待方
        first.simulate_disconnect()
        assert acc._chat_transport.fallback_client is second, (
            "旧连接迟到通知清掉了新连接"
        )
        await asyncio.sleep(0.05)
        assert not task2.done(), "旧连接迟到通知误杀了新连接的等待方"
        second.simulate_disconnect()
        with pytest.raises(BotSessionError):
            await asyncio.wait_for(task2, 2)

    @pytest.mark.asyncio
    async def test_dead_fallback_is_judged_unhealthy_without_notification(
        self, mock_bot_ws, stub_v3_patch
    ):
        """复用健康检查：连接已死但断连通知尚未送达（recv_loop 还在
        finally 路径上）时，_ensure_v3_fallback 不得返回死连接——就地
        失效收口（归属表清理）并重建。"""
        acc = self._acc_with_awaitable_main()
        await acc.inject_message("m", session_key="sk-att-5", attachments=[{"k": 1}])
        first = acc._chat_transport.fallback_client
        assert first is not None
        first.connected = False  # 死连接，未触发断连处理器

        await acc.inject_message("m2", session_key="sk-att-5", attachments=[{"k": 1}])

        second = acc._chat_transport.fallback_client
        assert second is not first, "死连接被复用"
        assert len(_StubV3Transport.instances) == 2
        assert len(second.inject_calls) == 1
        await acc.close()


class TestV3FallbackConcurrentInit:
    """R7-F4：不同 session 并发首发附件——fallback 的检查/建连/发布必须
    互斥且锁内重查，连接唯一；否则两条连接下场是身份守卫吞掉首连断连
    （等待者悬挂）、末连断连误杀另一条连接的全部归属 run。"""

    @pytest.mark.asyncio
    async def test_concurrent_first_attachments_share_single_connection(
        self, mock_bot_ws
    ):
        gated = asyncio.Event()
        _GatedV3Transport.gated_instances = []
        _GatedV3Transport.release = gated
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client_ws."
            "BotWebSocketClient",
            _GatedV3Transport,
        ):
            acc = _a2a_acc_with_stub_main_client()

            t1 = asyncio.create_task(
                acc.send_message(
                    "m1", session_key="sk-1", attachments=[{"k": 1}], wait_result=False
                )
            )
            t2 = asyncio.create_task(
                acc.send_message(
                    "m2", session_key="sk-2", attachments=[{"k": 1}], wait_result=False
                )
            )
            # 两个任务都进入 _ensure_v3_fallback、闸在 connect 上——
            # 此时构造数即建连竞态的仲裁点（必须只建一条）。
            await asyncio.sleep(0.1)
            constructed = len(_GatedV3Transport.gated_instances)
            gated.set()
            await t1
            await t2

        assert constructed == 1, (
            f"并发首发构造了 {constructed} 条连接（互斥缺失，应为 1）"
        )
        single = acc._chat_transport.fallback_client
        assert single is not None, "fallback 未发布"
        # 两个 run 都必须走这唯一的连接，归属都指向它
        assert len(single.chat_send_calls) == 2, "并发首发被分散到多条连接"
        assert "sk-1" in acc._chat_transport.routed_sessions
        assert "sk-2" in acc._chat_transport.routed_sessions
        # 唯一连接断连 → 归属按预期整体收口（不再有"各连各的"误杀面）
        single.simulate_disconnect()
        assert acc._chat_transport.routed_sessions == {}, "断连后归属未整体收口"
        await acc.close()
