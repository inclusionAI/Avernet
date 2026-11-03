"""AsyncChatClient - transport-agnostic chat client orchestration

支持同一 transport 连接上多个 sessionKey 并行收发消息，并由独立传输
策略负责连接、重连、请求路由与 transport 专属归属状态。

核心设计：
- 按 sessionKey 维护独立的 _SessionState（content、Event、agent_events 等）
- _on_chat / _on_agent 事件回调根据 payload.sessionKey 分发到对应 state
- 同一 sessionKey 并发时排队等待（而非硬拒绝），超时抛 ConcurrentSessionError
- 不同 sessionKey 的消息可并行，互不干扰
- 可选的并发信号量（max_concurrent_sessions）限制单连接总并发数，提供背压
- 连接断开自动重连（max_retries），exponential backoff
- 连接池可按 sandbox_id 复用已握手的 AsyncChatClient 实例

全部基于 asyncio，不使用任何 threading，不会阻塞 event loop。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from secbaas.community.api.sse import StreamChunk
from secbaas.community.logger import get_logger

from ..bot_interaction import BotInteractionService
from ._async_chat_client_transport import (
    ChatTransport,
    ChatTransportCallbacks,
)
from ._async_chat_events import AsyncChatEvents, _capture_trace_context
from ._chat_errors import ChatRequestError
from ._chat_errors import NotConnectedError as NotConnectedError
from ._session_key_matcher import SessionKeyMatcher
from ._session_state import SessionState

logger = get_logger("core-bot-run")


class ConcurrentSessionError(Exception):
    """同一 sessionKey 上并发发送消息超时时抛出。

    AsyncChatClient 默认排队等待同一 sessionKey 的前一个请求完成，
    等待超过 session_key_timeout 后抛出此异常。
    """


class BotSessionError(Exception):
    """WebSocket 会话以 error 状态终止时抛出。

    当 agent/chat 事件回调收到 state=error 时，send_message 在
    chat_complete 后检查 state.state 并抛出此异常，使上游调用方
    （BaasBotService / executor）能将 bot_run 标记为 FAILED 而非 COMPLETED。
    """


class AsyncChatClient(AsyncChatEvents):
    """Transport-agnostic chat client orchestration.

    支持同一 transport 连接上多个 sessionKey 并行收发消息。
    按 sessionKey 维护独立的 _SessionState，事件回调根据 payload.sessionKey 分发。
    全部基于 asyncio，不使用任何 threading。

    并发控制：
    - max_concurrent_sessions: 单连接最大并发会话数（0=不限），
      通过 asyncio.Semaphore 提供背压，防止单连接上过多并发请求压垮服务端。
    - session_key_timeout: 同一 sessionKey 的并发请求排队等待超时时间，
      超时后抛 ConcurrentSessionError（与旧行为兼容）。

    重连机制：
    - max_retries: 连接断开后自动重连次数（0=不重试）。
    - retry_base_backoff: 重连退避基数（秒），实际退避 = base * 2^(attempt-1)。

    使用示例:
        client = AsyncChatClient(transport=transport, verbose=True)
        await client.connect()
        try:
            content, agent_events = await client.send_message("你好", session_key="sess-1")
            logger.info(f"Response: {content}")
            logger.info(f"Agent events: {len(agent_events)}")
        finally:
            await client.close()
    """

    def __init__(
        self,
        transport: ChatTransport,
        verbose: bool = False,
        max_concurrent_sessions: int = 0,
        session_key_timeout: float = 30.0,
        max_retries: int = 1,
        retry_base_backoff: float = 0.5,
        ignore_case: bool = False,
        interaction_service: BotInteractionService | None = None,
        sensitive_bypass_keys_loader: Callable[[], tuple[str, ...]] | None = None,
    ):
        """初始化客户端

        Args:
            transport: 由装配层选择的传输能力；连接与鉴权由其独立持有
            verbose: 是否打印详细日志
            max_concurrent_sessions: 单连接最大并发会话数，0 表示不限
            session_key_timeout: 同一 sessionKey 并发等待超时（秒）
            max_retries: 连接断开后自动重连次数，0 表示不重试
            retry_base_backoff: 重连退避基数（秒）
            ignore_case: sessionKey 模糊匹配是否忽略大小写
            sensitive_bypass_keys_loader: 敏感日志豁免片段读取函数
                （由连接池注入，返回解析后的片段元组）
        """
        self._chat_transport = transport
        self.verbose = verbose

        self._max_concurrent_sessions = max_concurrent_sessions
        self._session_key_timeout = session_key_timeout
        self._max_retries = max_retries
        self._retry_base_backoff = retry_base_backoff
        self._ignore_case = ignore_case
        self._interaction_service = interaction_service
        self._sensitive_bypass_keys_loader = sensitive_bypass_keys_loader
        self._interaction_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

        # 并发信号量：限制单连接总并发会话数，提供背压
        self._concurrency_sem: asyncio.Semaphore | None = (
            asyncio.Semaphore(max_concurrent_sessions)
            if max_concurrent_sessions > 0
            else None
        )

        # Condition 保护 _sessions 和 _active_sessions 的并发访问，
        # 同时用于同一 sessionKey 排队等待（wait/notify 机制）
        self._condition = asyncio.Condition()

        # sessionKey → _SessionState 分流表
        self._sessions: dict[str, SessionState] = {}

        # sessionKey 模糊匹配器：服务端返回的 sessionKey 可能比客户端注册的长，
        # 通过 contains 匹配从 store 中回溯查找客户端注册的原始 key
        self._session_matcher = SessionKeyMatcher(
            self._sessions, ignore_case=ignore_case
        )

        # send_message 的并发保护：同一 sessionKey 同一时刻只能有一个在等回复
        # dict[str, None] — 仅用于 "是否存在" 判断，值无意义
        self._active_sessions: set[str] = set()

        # 重连状态标记
        self._reconnecting = False
        # 主动关闭标记：close() 时设为 True，阻止重连
        self._closed_intentionally = False
        # 后台重连监控任务
        self._reconnect_monitor: asyncio.Task[None] | None = None
        # 断连事件：底层客户端断连时 set，_reconnect_loop 等待此事件
        self._disconnect_event: asyncio.Event = asyncio.Event()

        transport.bind_events(
            ChatTransportCallbacks(
                events=self._event_bridge_snapshot(),
                on_disconnect=self._on_disconnect,
                on_sessions_failed=self._on_transport_sessions_failed,
            )
        )

    @property
    def uri(self) -> str:
        """Connection endpoint for diagnostics, owned by the transport."""
        return self._chat_transport.uri

    @property
    def is_connected(self) -> bool:
        """检查连接是否健康（供连接池健康检查使用）。"""
        return self._chat_transport.is_connected

    @property
    def transport_name(self) -> str:
        return self._chat_transport.transport_name

    @property
    def is_reconnecting(self) -> bool:
        """检查是否正在重连中（供连接池跳过重连中的连接使用）。"""
        return self._reconnecting

    @property
    def has_active_sessions(self) -> bool:
        """是否有活跃占据（供连接池过期清理/排空判断）。

        R6-1：除等待中的编排会话外，transport-managed 后台 body task
        （wait_result=False 的非等待发送）也算——sweep 不得把仍在服务
        真实 run 的连接当空闲收口。

        R7-F2：fallback 上的附件后台 run 同样占据本连接的服务面
        （归属标记随 run 存活，与 transport 的 has_running_runs 同口径）。
        """
        if len(self._active_sessions) > 0:
            return True
        return self._chat_transport.has_active_sessions()

    def tracks_session(self, session_key: str) -> bool:
        """该连接是否持有此 session 的在途运行——R4-1/R6-1/R7-F2 归属查询。

        三信号：①编排层 _sessions（调用方**等待期间**——等待中取消
        落在等待者上永远正确）；②底层 transport run 在途（**run 执行期**——
        wait_result=False 的 ACK 返回后 _sessions 即被 pop，但 body
        task 仍在消费流，CancelTask 的 engine_task_id 记录还活着）；
        ③v3 fallback 归属标记（**附件 run 执行期**——B3 reroute 的 run
        建在 v3 专用连接上，标记由后台终态帧释放，不随等待方收口）。
        只看①会把"非等待发送后取消"退回普通负载分配，命中无记录的
        连接 → RUN_NOT_TRACKED 永不发 CancelTask。
        """
        if session_key in self._sessions:
            return True
        return self._chat_transport.tracks_session(session_key)

    @property
    def active_session_count(self) -> int:
        """当前活跃会话数（供连接池负载均衡使用）。"""
        return len(self._active_sessions)

    def refresh_outbound_auth(self, headers: dict[str, str] | None) -> None:
        """Refresh authentication through the selected transport only."""
        self._chat_transport.refresh_outbound_auth(headers)

    async def connect(self) -> dict[str, Any]:
        """Connect through the explicitly selected transport strategy."""
        self._closed_intentionally = False
        self._disconnect_event.clear()

        if self.verbose:
            logger.info("Connecting...")

        try:
            hello = await self._chat_transport.connect()
        except Exception as e:
            logger.error(f"Connect failed: {e}")
            raise

        if self._max_retries > 0 and self._reconnect_monitor is None:
            self._reconnect_monitor = asyncio.create_task(self._reconnect_loop())

        if self.verbose:
            logger.info(
                f"Connected! Server: {hello.get('server', {}).get('host', 'unknown')}"
            )

        return hello

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def send_message(
        self,
        message: str,
        session_key: str | None = None,
        wait_result: bool = True,
        timeout: float | None = None,  # noqa: ASYNC109
        auth_token: str | None = None,
        app_id: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> tuple[str, list[Any]]:
        """发送消息并等待 chat 完成

        同一 sessionKey 的并发请求会排队等待前一个完成，超时抛
        ConcurrentSessionError。不同 sessionKey 的请求可并行。

        可选的并发信号量（max_concurrent_sessions > 0 时）会限制单连接
        上的总并发会话数，超出部分排队等待，提供背压。

        Args:
            message: 要发送的消息内容
            session_key: 会话 key，不传则自动生成
            wait_result: 是否等待结果，默认为 True
            timeout: 超时时间（秒），None 表示无限等待
            auth_token: 认证令牌，为空时传 OPEN_API:NOT_PROVIDED
            app_id: 应用标识，用于标识调用方应用
            chat_metadata: chat metadata
            attachments: 附件

        Returns:
            Tuple[content, agent_events]: 返回 (响应内容, agent事件列表)

        Raises:
            ConcurrentSessionError: 同一 sessionKey 并发等待超时
            NotConnectedError: 连接未建立或已断开
        """
        if session_key is None:
            session_key = f"{uuid.uuid4().hex}"

        if not self.is_connected:
            raise NotConnectedError(
                "Not connected. Call connect() first or wait for reconnection."
            )

        # 1. 获取并发信号量（背压门控）
        if self._concurrency_sem is not None:
            await self._concurrency_sem.acquire()

        try:
            # 2. 在 Condition 保护下等待同一 sessionKey 的前一个请求完成 + 注册
            #    原子操作：wait 和 register 在同一把锁内，消除竞争窗口
            async with self._condition:
                # 等待同一 sessionKey 的前一个请求完成
                while session_key in self._active_sessions:
                    try:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=self._session_key_timeout,
                        )
                    except TimeoutError:
                        raise ConcurrentSessionError(
                            f"Timed out waiting for session_key={session_key} "
                            f"(timeout={self._session_key_timeout}s)"
                        )
                    # wait() 返回后重新检查：可能被其他 sessionKey 的 notify 唤醒

                # 注册当前 sessionKey 为活跃（仍在锁内，无竞争窗口）
                self._active_sessions.add(session_key)
                # 捕获当前 OTel trace context，供回调中恢复
                trace_ctx = _capture_trace_context()
                if session_key in self._sessions:
                    state = self._sessions[session_key]
                    state.content = ""
                    state.state = ""
                    state.agent_payloads = []
                    state.last_stream_is_assistant = False
                    state.chat_complete.clear()
                    state.agent_complete.clear()
                    state.trace_context = trace_ctx
                else:
                    state = SessionState(trace_context=trace_ctx)
                    self._sessions[session_key] = state

            try:
                # 3. 检查连接状态
                if not self.is_connected:
                    raise NotConnectedError("Connection lost before sending message.")

                # 4. 发送消息
                try:
                    await self._send_chat_request(
                        session_key=session_key,
                        message=message,
                        auth_token=auth_token,
                        app_id=app_id,
                        timeout=timeout,
                        chat_metadata=chat_metadata,
                        attachments=attachments,
                        caller_label="send",
                    )
                except ChatRequestError:
                    state.chat_complete.set()
                    raise

                if not wait_result:
                    return state.content, state.agent_payloads

                # 5. 等待主对话事件完成
                if timeout:
                    await asyncio.wait_for(state.chat_complete.wait(), timeout=timeout)
                else:
                    # 无超时等待
                    await state.chat_complete.wait()

                # 6. 检查是否以 error 状态终止（带上引擎侧真实错误，
                #    否则上游 error 字段只剩 "error state" 外壳）
                if state.state == "error":
                    detail = (
                        f", error={state.error_message}" if state.error_message else ""
                    )
                    raise BotSessionError(
                        f"session ended with error state: "
                        f"session_key={session_key}{detail}"
                    )

                return state.content, state.agent_payloads

            finally:
                # 7. 清除 sessionKey 标记并唤醒等待者
                async with self._condition:
                    self._active_sessions.discard(session_key)
                    self._sessions.pop(session_key, None)
                    self._condition.notify_all()

        finally:
            # 8. 释放并发信号量
            if self._concurrency_sem is not None:
                self._concurrency_sem.release()

    async def chat_abort(
        self,
        session_key: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """发送 chat.abort 请求， Best-effort 通知 engine 取消 session/run。

        Args:
            session_key: 会话 key
            run_id: 可选的 run ID，透传给 engine

        Returns:
            engine 返回的原始响应 dict

        Raises:
            NotConnectedError: 连接未建立或已断开
        """
        if not self.is_connected:
            raise NotConnectedError(
                "Not connected. Call connect() first or wait for reconnection."
            )
        return await self._chat_transport.abort(session_key=session_key, run_id=run_id)

    async def send_message_stream(
        self,
        message: str,
        session_key: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        auth_token: str | None = None,
        app_id: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式发送消息，逐 chunk 产出 StreamChunk。

        与 send_message 相同的并发控制和 session 注册逻辑，
        但不等 chat_complete Event，而是从 state.stream_queue 消费。

        终止 chunk（type=final/error/agent_end）之后迭代器自然结束。
        """
        if session_key is None:
            session_key = f"{uuid.uuid4().hex}"

        if not self.is_connected:
            raise NotConnectedError(
                "Not connected. Call connect() first or wait for reconnection."
            )

        if self._concurrency_sem is not None:
            await self._concurrency_sem.acquire()

        try:
            async with self._condition:
                while session_key in self._active_sessions:
                    try:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=self._session_key_timeout,
                        )
                    except TimeoutError:
                        raise ConcurrentSessionError(
                            f"Timed out waiting for session_key={session_key} "
                            f"(timeout={self._session_key_timeout}s)"
                        )

                self._active_sessions.add(session_key)
                trace_ctx = _capture_trace_context()
                if session_key in self._sessions:
                    state = self._sessions[session_key]
                    state.content = ""
                    state.state = ""
                    state.agent_payloads = []
                    state.last_stream_is_assistant = False
                    state.chat_complete.clear()
                    state.agent_complete.clear()
                    state.trace_context = trace_ctx
                else:
                    state = SessionState(trace_context=trace_ctx)
                    self._sessions[session_key] = state

                # 流式模式：创建 queue 并绑定到 state
                queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
                state.stream_queue = queue

            try:
                if not self.is_connected:
                    raise NotConnectedError("Connection lost before sending message.")

                try:
                    await self._send_chat_request(
                        session_key=session_key,
                        message=message,
                        auth_token=auth_token,
                        app_id=app_id,
                        timeout=timeout,
                        chat_metadata=chat_metadata,
                        attachments=attachments,
                        caller_label="send_stream",
                    )
                except ChatRequestError as e:
                    state.chat_complete.set()
                    yield StreamChunk(
                        type="error",
                        content=f"chat.send failed: {e.error_code} - {e.error_message}",
                    )
                    return

                # 消费 stream_queue，逐 chunk 产出
                async for chunk in self._drain_stream_queue(queue, timeout):
                    yield chunk

            finally:
                async with self._condition:
                    self._active_sessions.discard(session_key)
                    self._sessions.pop(session_key, None)
                    self._condition.notify_all()

        finally:
            if self._concurrency_sem is not None:
                self._concurrency_sem.release()

    async def inject_message(
        self,
        message: str,
        session_key: str | None = None,
        auth_token: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> None:
        """注入消息到已有会话，不等待响应

        与 send_message 不同，inject_message 仅发送消息后立即返回，
        不等待 chat 完成事件，适用于注入系统指令、上下文补充等场景。

        注入消息同样受并发信号量约束（max_concurrent_sessions），
        但不受同一 sessionKey 排队机制限制（注入不等待响应，无会话状态竞争）。

        Args:
            message: 要注入的消息内容
            session_key: 会话 key，不传则自动生成
            auth_token: 认证令牌，为空时传 OPEN_API:NOT_PROVIDED
            chat_metadata: 对话元数据
            attachments: 附件
        """
        if session_key is None:
            session_key = f"{uuid.uuid4().hex}"

        if not self.is_connected:
            raise NotConnectedError(
                "Not connected. Call connect() first or wait for reconnection."
            )

        # 受并发信号量约束，提供背压
        if self._concurrency_sem is not None:
            await self._concurrency_sem.acquire()

        try:
            # 发送消息（不等待响应）
            logger.info("[inject] Injecting message: session_key=%s", session_key)
            inject_result = await self._chat_transport.inject(
                session_key=session_key,
                message=message,
                auth_token=auth_token,
                chat_metadata=chat_metadata,
                attachments=attachments,
            )
            logger.debug(
                "[inject] chat.inject raw result: session_key=%s result=%s",
                session_key,
                inject_result,
            )
            if not inject_result.get("ok"):
                error_payload = inject_result.get("error", {})
                logger.error(
                    "[inject] chat.inject failed: session_key=%s, error_code=%s, "
                    "error_message=%s",
                    session_key,
                    error_payload.get("code"),
                    error_payload.get("message"),
                )
                raise ChatRequestError(
                    message=f"chat.inject failed: {error_payload.get('code')} - {error_payload.get('message')}",
                    error_code=error_payload.get("code"),
                    error_message=error_payload.get("message"),
                    retryable=error_payload.get("retryable"),
                )
            logger.info(
                "[inject] injected message not wait, return as soon as possible"
            )
        finally:
            if self._concurrency_sem is not None:
                self._concurrency_sem.release()

    def _event_bridge_snapshot(self) -> dict[str, Any]:
        """Declarative callback bridge, available before any wire client connects."""
        return {
            "chat": self._on_chat,
            "agent": self._on_agent,
            "interaction.requested": self._on_interaction_requested,
            "interaction.resolved": self._on_interaction_resolved,
            "mode_transition.resolved": self._on_mode_transition_resolved,
            "error": self._on_error,
            "*": self._log_event,
        }

    async def close(self) -> None:
        """关闭连接并清理所有 session state。"""
        self._closed_intentionally = True

        # 取消重连监控任务
        if self._reconnect_monitor and not self._reconnect_monitor.done():
            self._reconnect_monitor.cancel()
            try:
                await self._reconnect_monitor
            except asyncio.CancelledError:
                pass
            self._reconnect_monitor = None

        await self._chat_transport.close()

        interaction_tasks = list(self._interaction_tasks.values())
        for task in interaction_tasks:
            task.cancel()
        if interaction_tasks:
            await asyncio.gather(*interaction_tasks, return_exceptions=True)
        self._interaction_tasks.clear()

        # 清理所有 session state，并唤醒等待中的协程
        async with self._condition:
            self._sessions.clear()
            self._active_sessions.clear()
            self._condition.notify_all()

    @staticmethod
    async def _drain_stream_queue(
        queue: asyncio.Queue[StreamChunk],
        timeout: float | None,
    ) -> AsyncIterator[StreamChunk]:
        """从 stream_queue 消费 StreamChunk，遇到终止 chunk 后停止。

        total timeout（timeout）：整个流的最大持续时间（秒），None 表示不限制。
        超时后 yield error chunk 并结束流。
        """
        terminal_types = {"final", "error"}
        deadline = None
        if timeout:
            deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if deadline is not None:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    logger.warning("[send_stream] total timeout exceeded")
                    yield StreamChunk(type="error", content="stream timeout")
                    return
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    logger.warning("[send_stream] total timeout exceeded")
                    yield StreamChunk(type="error", content="stream timeout")
                    return
            else:
                chunk = await queue.get()
            yield chunk
            if chunk.type in terminal_types:
                return

    # ── 私有方法 ──────────────────────────────────────────────────────────

    async def _send_chat_request(
        self,
        *,
        session_key: str,
        message: str,
        auth_token: str | None,
        app_id: str | None,
        timeout: float | None,
        chat_metadata: dict[str, str] | None,
        attachments: list[Any] | None,
        caller_label: str,
    ) -> dict[str, Any]:
        """统一封装 chat_send 调用：打印 result + 判断 ok + 构造异常。

        Transport 返回原始 result dict，并独立处理请求拒绝的路由清理。
        编排层统一负责调用方可见的日志与异常转换：
        1. 打印完整 ``result``（debug 级别，成功/失败都打）；
        2. ``ok=False`` 时：打 error 日志 + 从 result 构造 ``ChatRequestError`` 并抛出；
        3. ``ok=True`` 时：打 info 日志 + 返回 result。
        """
        logger.info(
            "[%s] Sending: session_key=%s, timeout=%s",
            caller_label,
            session_key,
            timeout,
        )
        send_result = await self._chat_transport.send(
            session_key=session_key,
            message=message,
            auth_token=auth_token,
            app_id=app_id,
            timeout_ms=int(timeout * 1000) if timeout else None,
            chat_metadata=chat_metadata,
            attachments=attachments,
        )

        logger.debug(
            "[%s] chat.send raw result: session_key=%s result=%s",
            caller_label,
            session_key,
            send_result,
        )

        if not send_result.get("ok"):
            error_payload = send_result.get("error", {})
            logger.error(
                "[%s] chat.send failed: session_key=%s, error_code=%s, error_message=%s",
                caller_label,
                session_key,
                error_payload.get("code"),
                error_payload.get("message"),
            )
            raise ChatRequestError(
                message=f"chat.send failed: {error_payload.get('code')} - {error_payload.get('message')}",
                error_code=error_payload.get("code"),
                error_message=error_payload.get("message"),
                retryable=error_payload.get("retryable"),
            )

        logger.info(
            "[%s] Sent successfully: session_key=%s",
            caller_label,
            session_key,
        )
        return send_result

    def _on_transport_sessions_failed(
        self, session_keys: tuple[str, ...], message: str, source: str
    ) -> None:
        """Only the facade may transition its own conversation states."""
        for session_key in session_keys:
            state = self._sessions.get(session_key)
            if state is not None and not state.chat_complete.is_set():
                self._handle_terminal_error(state, session_key, message, source)

    def _on_disconnect(self, event_name: str, payload: dict[str, Any]) -> None:
        """断连回调：通知 _reconnect_loop 立即感知断连。

        由 BotWebSocketClient._recv_loop 的 finally 块调用，
        替代 1 秒轮询，使断连检测和重连启动接近零延迟。

        同时把所有在途会话包装成终态 error（复用 _handle_terminal_error）：
        等待 chat_complete 的调用方立即得到 BotSessionError，
        流式消费者收到 error chunk 后结束，而不是等自身 timeout。
        已完成（chat_complete 已置位）的会话跳过，避免把
        已正常收尾的结果覆盖成 error。
        """
        logger.info(
            "[on_disconnect] event=%s, payload=%s, uri=%s",
            event_name,
            payload,
            self.uri,
        )
        for session_key, state in self._sessions.items():
            if state.chat_complete.is_set():
                continue
            self._handle_terminal_error(
                state, session_key, "connection lost", "disconnect"
            )
        self._notify_disconnect()

    def _notify_disconnect(self) -> None:
        """通知连接断开（由 _reconnect_loop 或外部调用）。

        设置 _disconnect_event，让 _reconnect_loop 立即被唤醒，
        而非等待下次轮询。
        """
        self._disconnect_event.set()

    async def _reconnect_loop(self) -> None:
        """后台监控连接状态，断连时自动重连。

        使用 _disconnect_event 事件驱动检测断连（替代轮询），
        当底层 transport 断连时被唤醒，以 exponential backoff
        尝试重建连接。重连成功后新请求可继续使用；在途会话已在
        _on_disconnect 中被标记为终态 error，不再等待自身 timeout。
        """
        while not self._closed_intentionally:
            # 等待连接断开：事件驱动，比轮询更及时
            if self.is_connected:
                # 还在线，等待断连事件或短暂检查
                self._disconnect_event.clear()
                # 用 wait_for 实现可中断的等待
                try:
                    await asyncio.wait_for(self._disconnect_event.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                # 重新检查状态
                if self.is_connected:
                    continue
                # 断连了，继续走重连逻辑

            # 连接已断开
            if self._closed_intentionally:
                break

            self._reconnecting = True
            reconnected = False

            for attempt in range(1, self._max_retries + 1):
                backoff = self._retry_base_backoff * (2 ** (attempt - 1))
                logger.info(
                    "[AsyncChatClient] Reconnect attempt %d/%d after %.1fs (uri=%s)",
                    attempt,
                    self._max_retries,
                    backoff,
                    self.uri,
                )
                await asyncio.sleep(backoff)

                if self._closed_intentionally:
                    break

                try:
                    # Replace only the main transport connection. Secondary
                    # running tasks and their ownership survive this operation.
                    await self._chat_transport.reconnect()
                    self._disconnect_event.clear()
                    reconnected = True
                    logger.info(
                        "[AsyncChatClient] Reconnected successfully "
                        "on attempt %d (uri=%s)",
                        attempt,
                        self.uri,
                    )
                    break
                except Exception as retry_exc:
                    logger.warning(
                        "[AsyncChatClient] Reconnect attempt %d failed: %s",
                        attempt,
                        retry_exc,
                    )

            self._reconnecting = False

            if not reconnected:
                # 重试耗尽，在途请求会通过自身 timeout 自然超时
                logger.error(
                    "[AsyncChatClient] All %d reconnect attempts exhausted, "
                    "giving up (uri=%s)",
                    self._max_retries,
                    self.uri,
                )
                break

            # 重连成功，继续监控循环
