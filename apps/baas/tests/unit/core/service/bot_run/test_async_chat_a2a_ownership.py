"""test async chat a2a ownership; focused regression tests and fixtures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    BotSessionError,
    NotConnectedError,
)

from ._async_chat_a2a_test_support import (
    _StubV3Transport,
    _TerminalOnSendV3,
)
from ._async_chat_a2a_test_support import (
    stub_v3_patch as stub_v3_patch,
)
from ._async_chat_coverage_support import (
    mock_bot_ws as mock_bot_ws,
)


class TestAttachFallbackRunOwnershipLifecycle:
    """R7-F2：附件 v3 fallback run 的归属锚定 run 生命周期，不随等待方
    收口。wait_result=False 的 ACK 返回不等于 run 结束——abort/resolve 仍
    必须路由 v3、池归属查询/sweep 保护仍必须认账；标记由后台终态帧
    （chat final/error）释放，而非 send_message 的 finally。"""

    async def _wait_until(self, predicate, timeout_s: float = 2.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("condition not met within timeout")

    def _acc_with_owned_a2a_main(self) -> AsyncChatClient:
        """transport=a2a + 显式诚实面主连接（归属查询/abort 不误报）。"""
        acc = AsyncChatClient(uri="ws://host/ws", max_retries=0, transport_mode="grey")
        acc._chat_transport._current_name = "a2a"
        acc._client = MagicMock()
        acc._client.connected = True
        acc._client.close = AsyncMock()
        acc._client.chat_abort = AsyncMock(return_value={"ok": False})
        acc._client.tracks_session = MagicMock(return_value=False)
        acc._client.has_running_runs = False
        # 桥面快照从主连接 _event_handlers 复刻——挂编排器真实 chat
        # 处理器，fallback 收引擎帧走真分发路径（非 mock）。
        acc._client._event_handlers = {"chat": acc._on_chat}
        return acc

    @pytest.mark.asyncio
    async def test_ack_return_keeps_v3_ownership_alive(
        self, mock_bot_ws, stub_v3_patch
    ):
        """主复现：send_message(wait_result=False, attachments) ACK 返回后
        ——标记存活、abort 随 run 走 v3、池两查询认账、不误发 A2A。"""
        acc = self._acc_with_owned_a2a_main()

        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )

        fallback = acc._chat_transport.fallback_client
        assert fallback is not None and fallback.chat_send_calls, "未重路由 v3"
        # ACK 后归属标记不得被等待方 finally 收口
        assert "sk-att" in acc._chat_transport.routed_sessions, "ACK 后归属标记被误清"
        # 池归属路由查询：找到持 run 的连接
        assert acc.tracks_session("sk-att") is True, "池归属查询不认后台附件 run"
        # sweep 排空判断：连接不得当空闲收口
        assert acc.has_active_sessions is True, "sweep 判断不认后台附件 run"
        # abort 必须发到 v3 fallback（A2A 客户端没有该 run）
        await acc.chat_abort("sk-att")
        assert fallback.chat_abort_calls == [("sk-att", {"run_id": None})], (
            "abort 未随 run 走 v3"
        )
        acc._client.chat_abort.assert_not_awaited()  # 被误发 A2A 主连接即失败
        await acc.close()

    @pytest.mark.asyncio
    async def test_background_terminal_frame_releases_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """后台终态帧（chat final——等待方已收口、state 为 None 的帧）是
        标记的释放点：归属随 run 终止结束，连接可被 sweep 收口。"""
        acc = self._acc_with_owned_a2a_main()

        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )

        fallback = acc._chat_transport.fallback_client
        chat_handler = fallback._event_handlers["chat"]
        # 引擎经 v3 连接推 final——此时 orchestrator 的 session state 已被
        # ACK 方 finally pop（state=None 分支是归属标记唯一见证者）
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "final",
                "message": {"content": [{"type": "text", "text": "ok"}]},
            }
        )

        assert "sk-att" not in acc._chat_transport.routed_sessions, "终态帧后标记泄漏"
        assert acc.tracks_session("sk-att") is False, "终态后归属查询仍认账"
        assert acc.has_active_sessions is False, "终态后连接仍不可收口"
        await acc.close()

    @pytest.mark.asyncio
    async def test_waiter_timeout_keeps_v3_ownership(self, mock_bot_ws, stub_v3_patch):
        """wait_result=True 超时：等待方虽收口，engine 侧 run 仍在执行
        ——归属必须存活（abort 仍路由 v3），由后台终态帧释放。"""
        acc = self._acc_with_owned_a2a_main()

        with pytest.raises(TimeoutError):
            await acc.send_message(
                "m",
                session_key="sk-att",
                attachments=[{"k": 1}],
                wait_result=True,
                timeout=0.05,
            )

        assert "sk-att" in acc._chat_transport.routed_sessions, "超时后归属标记被误清"
        await acc.chat_abort("sk-att")
        assert acc._chat_transport.fallback_client.chat_abort_calls == [
            ("sk-att", {"run_id": None})
        ]
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_engine_error_terminal_releases_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """等待中的 run 以 error 终态收口=生命周期结束——归属随释放。"""
        acc = self._acc_with_owned_a2a_main()

        task = asyncio.create_task(
            acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        chat_handler = acc._chat_transport.fallback_client._event_handlers["chat"]
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "error",
                "errorMessage": "engine boom",
                "errorCode": "E_BOOM",
            }
        )

        with pytest.raises(BotSessionError):
            await task
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "error 终态后标记泄漏"
        )
        await acc.close()

    @pytest.mark.asyncio
    async def test_send_failure_clears_ownership_no_stale_marker(
        self, mock_bot_ws, stub_v3_patch
    ):
        """chat_send 被引擎拒（run 从未开始）——不得残留归属标记（否则
        后续该 session 的 abort/resolve 会被误路由 v3）。"""
        acc = self._acc_with_owned_a2a_main()

        with patch.object(
            _StubV3Transport,
            "chat_send",
            AsyncMock(
                return_value={
                    "ok": False,
                    "error": {"code": "REJECTED", "message": "no"},
                }
            ),
        ):
            from secbaas.community.core.service.bot_run._async_chat_client import (
                ChatRequestError,
            )

            with pytest.raises(ChatRequestError):
                await acc.send_message(
                    "m",
                    session_key="sk-att",
                    attachments=[{"k": 1}],
                    wait_result=False,
                )

        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "失败发送残留归属标记"
        )
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    # ─────────── R8：归属释放必须锚定真实终态，不枚举等待方退出方式 ───────────

    @pytest.mark.asyncio
    async def test_inject_final_does_not_release_background_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R8-F1：后台附件 run 在途时，同 session 的注入 final（runId 带
        inject- 前缀 / stopReason=inject）不是主 run 终态——不得释放归属，
        否则主 run 的 abort 会被误路由 A2A 主连接。"""
        acc = self._acc_with_owned_a2a_main()
        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )
        fallback = acc._chat_transport.fallback_client
        chat_handler = fallback._event_handlers["chat"]

        # 两种注入终态形态，主 run 都必须在途认账
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "final",
                "runId": "inject-sidecar",
                "message": {"content": [{"type": "text", "text": "injected"}]},
            }
        )
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "final",
                "stopReason": "inject",
                "message": {"content": [{"type": "text", "text": "injected"}]},
            }
        )

        assert "sk-att" in acc._chat_transport.routed_sessions, (
            "注入 final 误释放主 run 归属"
        )
        assert acc.tracks_session("sk-att") is True, "归属查询因注入误翻"
        await acc.chat_abort("sk-att")
        assert fallback.chat_abort_calls == [("sk-att", {"run_id": None})], (
            "主 run 的 abort 被注入误导到错误连接"
        )
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_waiter_cancellation_keeps_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R8-F2a：取消本地等待协程（调用方 task.cancel() / 外层 wait_for
        超时都是 CancelledError 进 finally）不会取消已发到引擎的 run
        ——归属必须保留，abort 仍路由 v3。"""
        acc = self._acc_with_owned_a2a_main()

        task = asyncio.create_task(
            acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "sk-att" in acc._chat_transport.routed_sessions, (
            "取消等待方误删在途 run 归属"
        )
        await acc.chat_abort("sk-att")
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_stream_early_close_keeps_ownership(self, mock_bot_ws, stub_v3_patch):
        """R8-F2b：流式消费方在终态 chunk 之前提前关闭（break/超时/异常）
        ——引擎侧 run 仍在执行，finally 不得清归属。"""
        acc = self._acc_with_owned_a2a_main()

        chunks: list[StreamChunk] = []

        async def consume():
            agen = acc.send_message_stream(
                "m",
                session_key="sk-att",
                attachments=[{"k": 1}],
                timeout=5.0,
            )
            async for chunk in agen:
                chunks.append(chunk)
                break  # 终态前提前关闭
            # 显式收口：否则 async-gen finalizer 延迟执行，finally 时序
            # 不确定（假绿）——R8 的确定性形态必须当场触发 finally。
            await agen.aclose()

        t = asyncio.create_task(consume())
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        acc._chat_transport.fallback_client._event_handlers["chat"](
            {
                "sessionKey": "sk-att",
                "state": "delta",
                "deltaText": "部分响应",
                "message": {"content": [{"type": "text", "text": "部分响应"}]},
            }
        )
        await t
        assert chunks, "未消费到任何 chunk 就提前结束（fixture 失效）"

        assert "sk-att" in acc._chat_transport.routed_sessions, (
            "流式提前关闭误删在途 run 归属"
        )
        await acc.chat_abort("sk-att")
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_terminal_before_send_returns_releases_ownership(self, mock_bot_ws):
        """R8-F3：终态帧在 chat_send() 等待方恢复前被 WS 接收循环分派
        （state 仍在、终态走有 state 分支）——归属释放不得依赖"state
        已被等待方 pop"的形态，否则此后再无终态可触发清理，标记永久残留
        （连接无法排空、owner 悬挂）。"""
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client_ws."
            "BotWebSocketClient",
            _TerminalOnSendV3,
        ):
            acc = self._acc_with_owned_a2a_main()
            await acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
            )

        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "终态已到达但归属永久残留"
        )
        assert acc.tracks_session("sk-att") is False, "终态后归属查询仍认账"
        assert acc.has_active_sessions is False, "终态后连接仍不可排空"
        await acc.close()

    # ─────────── R9：归属释放的时序/key 规范化/终态通道三面 ───────────

    @pytest.mark.asyncio
    async def test_stream_disconnect_while_enqueued_raises_notconnected(
        self, mock_bot_ws
    ):
        """R9-F1：流式请求通过入口连接检查后在并发/同 session 排队期间
        断连——恢复执行必须抛 NotConnectedError，不得被 finally 里未初始化
        变量的 UnboundLocalError 吞掉真实断连原因。"""
        acc = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        acc._client = MagicMock()
        acc._client.connected = True
        acc._client.close = AsyncMock()

        # 先占住同 session 让生成器排队（入口检查此时已通过）
        async with acc._condition:
            acc._active_sessions.add("sk-att")

        async def consume():
            agen = acc.send_message_stream(
                "m", session_key="sk-att", attachments=[{"k": 1}], timeout=5.0
            )
            async for chunk in agen:
                pass

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.1)  # t 已在 condition 排队
        acc._client.connected = False  # 排队期间断连
        async with acc._condition:
            acc._active_sessions.discard("sk-att")
            acc._condition.notify_all()

        with pytest.raises(NotConnectedError):
            await t
        await acc.close()

    @pytest.mark.asyncio
    async def test_decorated_terminal_key_still_releases_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R9-F2：引擎终态帧的 sessionKey 是发送 key 的前后缀装饰形态
        （SessionKeyMatcher 语义）——释放必须能从装饰 key 归并回发送时的
        登记键，等待中与后台两态都不许残留。"""
        acc = self._acc_with_owned_a2a_main()
        decorated = "agent:claude-code-ws:session:sk-att:user:claude-code-ws"
        final_frame = {
            "sessionKey": decorated,
            "state": "final",
            "message": {"content": [{"type": "text", "text": "done"}]},
        }

        # 后台态：等待方已收口，state=None 分支收帧
        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )
        assert "sk-att" in acc._chat_transport.routed_sessions
        acc._chat_transport.fallback_client._event_handlers["chat"](final_frame)
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "装饰 key 的后台终态未释放归属"
        )
        assert acc.has_active_sessions is False

        # 等待中态：装饰 key 终态唤醒等待方，归属同样释放
        task = asyncio.create_task(
            acc.send_message(
                "m2", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: len(acc._chat_transport.fallback_client.chat_send_calls) == 2
        )
        acc._chat_transport.fallback_client._event_handlers["chat"](final_frame)
        content, _payloads = await task
        assert content == "done"
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "装饰 key 的等待中终态未释放归属"
        )
        await acc.close()

    @pytest.mark.asyncio
    async def test_agent_and_error_terminal_channels_release_ownership(
        self, mock_bot_ws
    ):
        """R9-F3a：终态不保证走 chat 通道（_derive_event_name 可派发为
        agent，WS 转发层遇终态直接结束）——agent final/error 与 error 通道
        必须同样释放归属，后台态（state=None）不许漏。"""
        acc = self._acc_with_owned_a2a_main()
        acc._client._event_handlers = {
            "chat": acc._on_chat,
            "agent": acc._on_agent,
            "error": acc._on_error,
        }
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client_ws."
            "BotWebSocketClient",
            _StubV3Transport,
        ):
            # agent final 通道（后台态）
            await acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
            )
            acc._chat_transport.fallback_client._event_handlers["agent"](
                {"sessionKey": "sk-att", "stream": "assistant", "state": "final"}
            )
            assert "sk-att" not in acc._chat_transport.routed_sessions, (
                "agent final 通道未释放归属"
            )

            # error 通道（后台态）
            await acc.send_message(
                "m2", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
            )
            acc._chat_transport.fallback_client._event_handlers["error"](
                {"sessionKey": "sk-att", "state": "error", "errorMessage": "boom"}
            )
            assert "sk-att" not in acc._chat_transport.routed_sessions, (
                "error 通道未释放归属"
            )
        await acc.close()

    @pytest.mark.asyncio
    async def test_agent_final_wake_releases_ownership_in_wait(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R9-F3b：等待方被 agent final 唤醒（仅 agent 通道终结、无 chat
        final）——run 已终结，归属必须释放。"""
        acc = self._acc_with_owned_a2a_main()
        acc._client._event_handlers = {
            "chat": acc._on_chat,
            "agent": acc._on_agent,
            "error": acc._on_error,
        }

        task = asyncio.create_task(
            acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        acc._chat_transport.fallback_client._event_handlers["agent"](
            {
                "sessionKey": "sk-att",
                "stream": "assistant",
                "state": "final",
                "message": {"content": [{"text": "agent done"}]},
            }
        )
        await task
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "agent final 唤醒后归属未释放"
        )
        assert acc.has_active_sessions is False
        await acc.close()

    # ─────────── R10：归属释放只认 v3 fallback 来源帧 ───────────

    @pytest.mark.asyncio
    async def test_a2a_terminal_spares_fallback_ownership_of_prefix_session(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R10：A2A 主连接与 v3 fallback 共用事件 handler，但归属标记只属
        于 fallback 上的 run——A2A session（裸 key，自身是另一 fallback
        session 的超串，如 chat-10 ⊃ chat-1）的终态经主连接分发时，不得
        在归属子表 contains 误中前缀 session 而清掉 fallback 的归属。"""
        acc = self._acc_with_owned_a2a_main()
        acc._client._event_handlers["chat"] = acc._on_chat  # 双注册同形

        # chat-1：附件 run，后台在途于 v3 fallback
        await acc.send_message(
            "m1", session_key="chat-1", attachments=[{"k": 1}], wait_result=False
        )
        assert "chat-1" in acc._chat_transport.routed_sessions
        fallback = acc._chat_transport.fallback_client

        # chat-10：A2A 主连接 run（裸 key，包含 chat-1 为前缀）终结
        acc._client._event_handlers["chat"](
            {
                "sessionKey": "chat-10",
                "state": "final",
                "message": {"content": [{"type": "text", "text": "done"}]},
            }
        )

        assert "chat-1" in acc._chat_transport.routed_sessions, (
            "A2A session 终态误清了 fallback 前缀 session 的归属"
        )
        assert acc.tracks_session("chat-1") is True
        # chat-1 的取消仍必须路由到持有 run 的 v3 fallback
        await acc.chat_abort("chat-1")
        assert fallback.chat_abort_calls == [("chat-1", {"run_id": None})], (
            "跨 session 误清理后 abort 被错发 A2A 主连接"
        )
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()
