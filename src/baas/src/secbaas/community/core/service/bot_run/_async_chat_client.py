"""AsyncChatClient - WebSocket 聊天客户端封装（纯异步版本）

支持同一 WS 连接上多个 sessionKey 并行收发消息。

核心设计：
- 按 sessionKey 维护独立的 _SessionState（content、Event、agent_events 等）
- _on_chat / _on_agent 事件回调根据 payload.sessionKey 分发到对应 state
- 同一 sessionKey 并发时排队等待（而非硬拒绝），超时抛 ConcurrentSessionError
- 不同 sessionKey 的消息可并行，互不干扰
- 可选的并发信号量（max_concurrent_sessions）限制单连接总并发数，提供背压
- WS 断连自动重连（max_retries），exponential backoff
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
from secbaas.community.tracer import get_tracer_plugin

from ._bot_websocket_client import BotWebSocketClient
from ._session_key_matcher import SessionKeyMatcher
from ._session_state import _SessionState

logger = get_logger("core-bot-run")


def _capture_trace_context() -> Any:
    """捕获当前 trace context，供后续回调中恢复。

    在 send_message 注册 session 时调用，保存当前请求的 trace context。
    返回值是不透明的 context 对象，传给 _with_session_trace 恢复。
    """
    return get_tracer_plugin().capture_context()


def _with_session_trace(method_name: str = "_on_event") -> Callable[..., Any]:
    """装饰器：从 payload 中查找 session state，恢复 trace context 后执行方法。

    适用于 _on_chat / _on_agent 等 WS 回调，这些回调在 _recv_loop 后台 Task
    中执行，无 active trace context。装饰器自动：
      1. 从 payload.sessionKey 查找 _SessionState（支持模糊匹配）
      2. 恢复 state 中保存的 trace context，使日志 traceid 关联原始请求
      3. 将 state 作为关键字参数传入被装饰方法
      4. 执行完毕后还原上下文
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(self: AsyncChatClient, *args: Any) -> None:
            # 兼容两种签名：(payload,) 和 (event_name, payload)
            if len(args) == 2:
                event_name, payload = args[0], args[1]
            else:
                event_name = None
                payload = args[0]

            session_key = (
                payload.get("sessionKey", "") if isinstance(payload, dict) else ""
            )
            match_result = (
                self._session_matcher.find(session_key) if session_key else None
            )
            state = match_result.state if match_result else None

            tracer = get_tracer_plugin()
            token = None
            if state is not None and state.trace_context is not None:
                token = tracer.attach_context(state.trace_context)
            try:
                if event_name is not None:
                    fn(self, event_name, payload, session_key=session_key, state=state)
                else:
                    fn(self, payload, session_key=session_key, state=state)
            finally:
                if token is not None:
                    tracer.detach_context(token)

        wrapper.__name__ = method_name
        wrapper.__qualname__ = f"AsyncChatClient.{method_name}"
        return wrapper

    return decorator


class ConcurrentSessionError(Exception):
    """同一 sessionKey 上并发发送消息超时时抛出。

    AsyncChatClient 默认排队等待同一 sessionKey 的前一个请求完成，
    等待超过 session_key_timeout 后抛出此异常。
    """


class NotConnectedError(Exception):
    """连接未建立或已断开时抛出。"""


class AsyncChatClient:
    """WebSocket 聊天客户端封装类（纯异步版本）

    支持同一 WS 连接上多个 sessionKey 并行收发消息。
    按 sessionKey 维护独立的 _SessionState，事件回调根据 payload.sessionKey 分发。
    全部基于 asyncio，不使用任何 threading。

    并发控制：
    - max_concurrent_sessions: 单连接最大并发会话数（0=不限），
      通过 asyncio.Semaphore 提供背压，防止单连接上过多并发请求压垮 WS Server。
    - session_key_timeout: 同一 sessionKey 的并发请求排队等待超时时间，
      超时后抛 ConcurrentSessionError（与旧行为兼容）。

    重连机制：
    - max_retries: WS 断连后自动重连次数（0=不重试）。
    - retry_base_backoff: 重连退避基数（秒），实际退避 = base * 2^(attempt-1)。

    使用示例:
        client = AsyncChatClient(uri, headers=headers, verbose=True)
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
        uri: str,
        headers: dict[str, str] | None = None,
        client_id: str | None = None,
        client_version: str = "1.0.0",
        verbose: bool = False,
        max_concurrent_sessions: int = 0,
        session_key_timeout: float = 30.0,
        max_retries: int = 1,
        retry_base_backoff: float = 0.5,
        ignore_case: bool = False,
    ):
        """初始化客户端

        Args:
            uri: WebSocket URI
            headers: 请求头（如 Cookie）
            client_id: 客户端 ID，不传则自动生成
            client_version: 客户端版本
            verbose: 是否打印详细日志
            max_concurrent_sessions: 单连接最大并发会话数，0 表示不限
            session_key_timeout: 同一 sessionKey 并发等待超时（秒）
            max_retries: WS 断连后自动重连次数，0 表示不重试
            retry_base_backoff: 重连退避基数（秒）
            ignore_case: sessionKey 模糊匹配是否忽略大小写
        """
        self.uri = uri
        self.headers = headers or {}
        self.client_id = client_id or f"client-{uuid.uuid4().hex[:8]}"
        self.client_version = client_version
        self.verbose = verbose

        self._max_concurrent_sessions = max_concurrent_sessions
        self._session_key_timeout = session_key_timeout
        self._max_retries = max_retries
        self._retry_base_backoff = retry_base_backoff
        self._ignore_case = ignore_case

        # 并发信号量：限制单连接总并发会话数，提供背压
        self._concurrency_sem: asyncio.Semaphore | None = (
            asyncio.Semaphore(max_concurrent_sessions)
            if max_concurrent_sessions > 0
            else None
        )

        self._client: BotWebSocketClient | None = None

        # Condition 保护 _sessions 和 _active_sessions 的并发访问，
        # 同时用于同一 sessionKey 排队等待（wait/notify 机制）
        self._condition = asyncio.Condition()

        # sessionKey → _SessionState 分流表
        self._sessions: dict[str, _SessionState] = {}

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
        # 断连事件：BotWebSocketClient 断连时 set，_reconnect_loop 等待此事件
        self._disconnect_event: asyncio.Event = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        """检查连接是否健康（供连接池健康检查使用）。"""
        return self._client is not None and self._client.connected

    @property
    def is_reconnecting(self) -> bool:
        """检查是否正在重连中（供连接池跳过重连中的连接使用）。"""
        return self._reconnecting

    @property
    def has_active_sessions(self) -> bool:
        """是否有正在等待回复的活跃会话（供连接池过期清理使用）。"""
        return len(self._active_sessions) > 0

    @property
    def active_session_count(self) -> int:
        """当前活跃会话数（供连接池负载均衡使用）。"""
        return len(self._active_sessions)

    async def connect(self) -> dict[str, Any]:
        """连接到服务器

        Returns:
            握手响应
        """
        if self._client is not None:
            raise RuntimeError("Already connected")

        self._closed_intentionally = False
        self._disconnect_event.clear()
        _client = BotWebSocketClient(
            uri=self.uri,
            client_id=self.client_id,
            client_version=self.client_version,
            headers=self.headers,
        )

        _client.on_event("chat", self._on_chat)
        _client.on_event("agent", self._on_agent)
        _client.on_event("error", self._on_error)
        _client.on_event("*", self._log_event)
        _client.on_disconnect(self._on_disconnect)

        if self.verbose:
            logger.info("Connecting...")

        # 直接 await 异步连接，不再需要 run_in_executor
        hello = await _client.connect()
        self._client = _client
        # 启动后台重连监控（仅在 max_retries > 0 时）
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
                    state = _SessionState(trace_context=trace_ctx)
                    self._sessions[session_key] = state

            try:
                # 3. 检查连接状态
                if not self.is_connected:
                    raise NotConnectedError("Connection lost before sending message.")

                # 4. 发送消息
                #
                logger.info(
                    "[send] Sending message: session_key=%s, wait_result=%s, timeout=%s",
                    session_key,
                    wait_result,
                    timeout,
                )
                assert self._client is not None
                send_result = await self._client.chat_send(
                    session_key=session_key,
                    message=message,
                    auth_token=auth_token,
                    app_id=app_id,
                    timeout_ms=int(timeout * 1000) if timeout else None,
                    chat_metadata=chat_metadata,
                )

                logger.info(
                    "[send] Sent successfully: session_key=%s, result=%s",
                    session_key,
                    send_result,
                )

                if not wait_result:
                    return state.content, state.agent_payloads

                # 5. 等待主对话事件完成
                if timeout:
                    await asyncio.wait_for(state.chat_complete.wait(), timeout=timeout)
                else:
                    # 无超时等待
                    await state.chat_complete.wait()

                return state.content, state.agent_payloads

            finally:
                # 6. 清除 sessionKey 标记并唤醒等待者
                async with self._condition:
                    self._active_sessions.discard(session_key)
                    self._sessions.pop(session_key, None)
                    self._condition.notify_all()

        finally:
            # 7. 释放并发信号量
            if self._concurrency_sem is not None:
                self._concurrency_sem.release()

    async def send_message_stream(
        self,
        message: str,
        session_key: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        auth_token: str | None = None,
        app_id: str | None = None,
        chat_metadata: dict[str, str] | None = None,
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
                    state = _SessionState(trace_context=trace_ctx)
                    self._sessions[session_key] = state

                # 流式模式：创建 queue 并绑定到 state
                queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
                state.stream_queue = queue

            try:
                if not self.is_connected:
                    raise NotConnectedError("Connection lost before sending message.")

                logger.info(
                    "[send_stream] Sending: session_key=%s, timeout=%s",
                    session_key,
                    timeout,
                )
                assert self._client is not None
                await self._client.chat_send(
                    session_key=session_key,
                    message=message,
                    auth_token=auth_token,
                    app_id=app_id,
                    timeout_ms=int(timeout * 1000) if timeout else None,
                    chat_metadata=chat_metadata,
                )

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
            assert self._client is not None
            await self._client.chat_inject(
                session_key=session_key,
                message=message,
                auth_token=auth_token,
                chat_metadata=chat_metadata,
            )
            logger.info(
                "[inject] injected message not wait, return as soon as possible"
            )
        finally:
            if self._concurrency_sem is not None:
                self._concurrency_sem.release()

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

        if self._client:
            await self._client.close()
            self._client = None

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

    def _get_session(self, session_key: str) -> _SessionState | None:
        """获取指定 sessionKey 的状态（支持模糊匹配）。"""
        result = self._session_matcher.find(session_key)
        return result.state if result else None

    @staticmethod
    def _emit_stream_chunk(state: _SessionState, chunk: StreamChunk) -> None:
        if state.stream_queue is not None:
            state.stream_queue.put_nowait(chunk)

    @staticmethod
    def _handle_terminal_error(
        state: _SessionState, session_key: str, error_msg: str, source: str
    ) -> None:
        msg = error_msg or f"{source} error"
        logger.warning("[%s] error: sessionKey=%s, errMsg=%s", source, session_key, msg)
        state.state = "error"
        state.chat_complete.set()
        AsyncChatClient._emit_stream_chunk(
            state, StreamChunk(type="error", content=msg)
        )

    @_with_session_trace("_on_chat")
    def _on_chat(
        self,
        payload: dict[str, Any],
        *,
        session_key: str,
        state: _SessionState | None,
    ) -> None:
        """内部 chat 事件处理器。

        根据 payload 中的 sessionKey 分发到对应的 _SessionState。
        如果没有 sessionKey 或找不到对应的 state，则忽略该事件。

        由 @_with_session_trace 装饰器自动查找 state 并恢复 trace context，
        使日志的 traceid 能正确关联原始请求。
        """
        logger.debug("session cnt=%s", len(self._sessions))

        chat_state = payload.get("state", "")
        event_run_id = payload.get("runId") or payload.get("run_id")
        message = payload.get("message", {})
        content = message.get("content", [])

        # 提取文本内容
        text = ""
        if content and len(content) > 0:
            text = content[0].get("text", "")

        # No state associated with this session key
        if state is None:
            logger.warning(
                f"[chat] No session state for sessionKey={session_key}, "
                f"state={chat_state}, text_len={len(text)}, ignore_case={self._ignore_case}"
            )
            return

        if chat_state == "delta":
            state.content = text
            # Only the incremental delta text goes downstream. BCS self-accumulates
            # deltas by run_id, so we must NOT send the cumulative `message` (it
            # would persist growing supersets). No metadata needed.
            delta_text = payload.get("deltaText") or payload.get("delta") or ""
            self._emit_stream_chunk(
                state, StreamChunk(type="delta", content=delta_text)
            )
            if self.verbose:
                logger.info(f"[chat] delta: sessionKey={session_key}, text={text[:80]}")
        elif chat_state == "final":
            if isinstance(event_run_id, str) and event_run_id.startswith("inject-"):
                logger.info(
                    "[chat] final with inject runId, skip chat_complete: "
                    "sessionKey=%s, runId=%s",
                    session_key,
                    event_run_id,
                )
            elif payload.get("stopReason") and payload.get("stopReason") == "inject":
                logger.info(
                    "[chat] final with stopReason=inject, skip chat_complete: "
                    "sessionKey=%s",
                    session_key,
                )
            else:
                state.content = text
                state.state = chat_state
                state.chat_complete.set()
                # Keep the full final text on the chunk — StreamChunk is engine-
                # neutral and other consumers (default SSE converter, non-stream
                # callers) rely on it. The BCN converter simply ignores final
                # content (BCS flushes its accumulated delta buffer instead).
                self._emit_stream_chunk(state, StreamChunk(type="final", content=text))
                if self.verbose:
                    logger.info(
                        f"[chat] final: sessionKey={session_key}, state={chat_state}"
                    )
        elif chat_state == "error":
            state.content = text
            state.state = chat_state
            state.chat_complete.set()
            self._emit_stream_chunk(state, StreamChunk(type="error", content=text))
            if self.verbose:
                logger.info(
                    f"[chat] error: sessionKey={session_key}, payload={payload}"
                )
        else:
            if self.verbose:
                logger.info(
                    f"[chat] ignored: sessionKey={session_key}, state={chat_state}"
                )

    @_with_session_trace("_on_agent")
    def _on_agent(
        self,
        payload: dict[str, Any],
        *,
        session_key: str,
        state: _SessionState | None,
    ) -> None:
        """内部 agent 事件处理器。

        根据 payload 中的 sessionKey 分发到对应的 _SessionState。

        由 @_with_session_trace 装饰器自动查找 state 并恢复 trace context，
        使日志的 traceid 能正确关联原始请求。
        """
        if state is None:
            logger.warning(
                "[agent] No session state for sessionKey=%s, ignore_case=%s",
                session_key,
                self._ignore_case,
            )
            return

        stream = payload.get("stream", "")
        agent_state = payload.get("state", "")
        if agent_state and agent_state == "error":
            self._handle_terminal_error(
                state, session_key, payload.get("errorMessage", ""), "agent"
            )
            return

        if agent_state and agent_state == "final":
            # agent final 事件视为整个会话的结束标志：
            # 设置 agent_complete + chat_complete 唤醒等待方，并 emit final chunk
            # 让流式迭代器终止。
            state.state = "final"
            state.agent_complete.set()
            state.chat_complete.set()
            content = payload.get("message", {}).get("content", [])
            text = content[0].get("text", "") if content else ""
            state.content = text
            self._emit_stream_chunk(state, StreamChunk(type="final", content=text))
            if self.verbose:
                logger.info(
                    "[agent] final: sessionKey=%s, stream=%s", session_key, stream
                )
            return

        if self.verbose:
            logger.info(
                "[agent] sessionKey=%s, stream=%s, has_state=%s",
                session_key,
                stream,
                state is not None,
            )

        # 流式模式：推送 agent 帧到 stream_queue。chunk.type="agent" 已表达事件
        # 类型；engine_frame 直接存原始 payload（thinking/tool/lifecycle 的
        # stream+data），converter 按 chunk.type 分发、把 engine_frame 当 payload 处理。
        self._emit_stream_chunk(
            state,
            StreamChunk(type="agent", content="", metadata={"engine_frame": payload}),
        )

        if stream == "tool":
            state.last_stream_is_assistant = False
            data = payload.get("data", "{}")
            phase = data.get("phase", "")
            if phase == "result":
                state.agent_payloads.append(payload)
        elif stream == "assistant":
            if state.last_stream_is_assistant:
                # Replace
                state.agent_payloads[-1] = payload
            else:
                state.agent_payloads.append(payload)
            # Set the tag
            state.last_stream_is_assistant = True
        elif stream == "lifecycle":
            state.last_stream_is_assistant = False
            data = payload.get("data", "{}")
            phase = data.get("phase", "")
            if phase == "end":
                state.agent_complete.set()
        else:
            state.last_stream_is_assistant = False

    @_with_session_trace("_on_error")
    def _on_error(
        self,
        payload: dict[str, Any],
        *,
        session_key: str,
        state: _SessionState | None,
    ) -> None:
        """error 处理器
        Args:
            payload: 事件载荷
            session_key: 会话 key（由装饰器注入）
            state: 会话状态（由装饰器注入）
        """
        if state is None:
            logger.warning(
                "[error] No session state for sessionKey=%s, ignore_case=%s",
                session_key,
                self._ignore_case,
            )
            return
        agent_state = payload.get("state", "")
        if agent_state and agent_state == "error":
            self._handle_terminal_error(
                state, session_key, payload.get("errorMessage", ""), "error"
            )
            return

    @_with_session_trace("_log_event")
    def _log_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        session_key: str,
        state: _SessionState | None,
    ) -> None:
        """兜底事件处理器

        监听所有未被专门处理的事件，记录日志便于排查。
        BotWebSocketClient 的通配符 "*" handler 签名为 (event_name, payload)。

        由 @_with_session_trace 装饰器自动查找 state 并恢复 trace context，
        使日志的 traceid 能正确关联原始请求。

        Args:
            event_name: 事件名称（如 "error", "system" 等）
            payload: 事件载荷
            session_key: 会话 key（由装饰器注入）
            state: 会话状态（由装饰器注入）
        """
        # 含敏感内容的字段按事件类型过滤
        _sensitive_keys: dict[str, tuple[str, ...]] = {
            "chat": ("message", "deltaText", "delta"),
            "agent": ("data",),
        }
        sensitive_keys = _sensitive_keys.get(event_name)
        if sensitive_keys:
            safe_payload = {k: v for k, v in payload.items() if k not in sensitive_keys}
            logger.info(f"[log_event] event={event_name}, payload={safe_payload}")
        else:
            logger.info(f"[log_event] event={event_name}, payload={payload}")

    def _on_disconnect(self, event_name: str, payload: dict[str, Any]) -> None:
        """断连回调：通知 _reconnect_loop 立即感知断连。

        由 BotWebSocketClient._recv_loop 的 finally 块调用，
        替代 1 秒轮询，使断连检测和重连启动接近零延迟。

        同时向所有活跃的 stream_queue 推送 error chunk，
        使流式消费者不会无限等待。
        """
        logger.info(
            "[on_disconnect] event=%s, payload=%s, uri=%s",
            event_name,
            payload,
            self.uri,
        )
        for state in self._sessions.values():
            if state.stream_queue is not None:
                state.stream_queue.put_nowait(
                    StreamChunk(type="error", content="connection lost")
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
        当底层 BotWebSocketClient 断连时被唤醒，以 exponential backoff
        尝试重建连接。重连成功后新请求可继续使用；在途请求依赖自身
        timeout 自然超时——这是 best-effort 行为。
        """
        while not self._closed_intentionally:
            # 等待连接断开：事件驱动，比轮询更及时
            if self._client is not None and self._client.connected:
                # 还在线，等待断连事件或短暂检查
                self._disconnect_event.clear()
                # 用 wait_for 实现可中断的等待
                try:
                    await asyncio.wait_for(self._disconnect_event.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                # 重新检查状态
                if self._client is not None and self._client.connected:
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
                    # 清理旧连接
                    if self._client is not None:
                        try:
                            await self._client.close()
                        except Exception:
                            pass
                        self._client = None

                    # 重建连接
                    new_client = BotWebSocketClient(
                        uri=self.uri,
                        client_id=self.client_id,
                        client_version=self.client_version,
                        headers=self.headers,
                    )
                    new_client.on_event("chat", self._on_chat)
                    new_client.on_event("agent", self._on_agent)
                    new_client.on_event("error", self._on_error)
                    new_client.on_event("*", self._log_event)
                    new_client.on_disconnect(self._on_disconnect)

                    await new_client.connect()
                    self._client = new_client
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
