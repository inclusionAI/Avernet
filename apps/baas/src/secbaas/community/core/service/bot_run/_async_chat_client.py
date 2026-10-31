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
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from secbaas.community.api.sse import StreamChunk
from secbaas.community.logger import get_logger
from secbaas.community.tracer import get_tracer_plugin

from ..bot_interaction import BotInteractionService
from ._bot_websocket_client import BotWebSocketClient, ChatRequestError
from ._interaction_protocol import (
    EngineInteractionRequestedEvent,
    EngineInteractionResolvedEvent,
)
from ._session_key_matcher import SessionKeyMatcher
from ._session_state import SessionState

logger = get_logger("core-bot-run")


def _strip_ws_endpoint_suffix(path: str) -> str:
    """剥去路径尾部的 WS endpoint 后缀，保留其前的一切路由前缀。

    后缀词汇集与 _claw_service._build_ws_url / adapter.ws_path() 同源：
      - ``/api/{engine}/ws``（默认与 openclaw/claude_code adapter）
      - ``/api/ws``（aicoding adapter）
      - ``/ws``（直连形态，无前缀）
    末段不是 ``ws``（未知自定义 ws_path）则不剥——base 维持旧语义
    （host 根），由能力探测诚实回退，绝不猜前缀归属。
    """
    segments = path.strip("/").split("/") if path.strip("/") else []
    if not segments or segments[-1] != "ws":
        return ""
    if len(segments) >= 3 and segments[-3] == "api" and segments[-2] != "api":
        # /api/{engine}/ws 与 /api/ws（aicoding 的 engine 段就是 "ws"）
        segments = segments[:-3]
    elif len(segments) >= 2 and segments[-2] == "api":
        segments = segments[:-2]
    else:
        segments = segments[:-1]
    return "/" + "/".join(segments) if segments else ""


def _a2a_base_url_from_ws(uri: str) -> str | None:
    """ws(s)://host:port{prefix}/ws-endpoint → http(s)://host:port{prefix}。

    A2A 端点与 v3 WS 端点同进程同端口（engine adapter :20003 单实例双面）；
    真实调用方 _build_ws_url 产出 ``wss://proxy/proxypass/<target>{ws_path}``
    形状——代理路由前缀必须保留在 base 上（只剥 WS endpoint 后缀，从后向前
    按词汇集剥），否则 card/RPC 打到代理根，grey 永远回退、on 连不上。
    """
    if not isinstance(uri, str) or "://" not in uri:
        return None
    scheme, _, rest = uri.partition("://")
    if scheme not in ("ws", "wss"):
        return None
    host_port, _, raw_path = rest.partition("/")
    prefix = _strip_ws_endpoint_suffix("/" + raw_path) if raw_path else ""
    return ("https://" if scheme == "wss" else "http://") + host_port + prefix


def _public_interaction_envelope(
    envelope: dict[str, object],
    *,
    baas_interaction_id: str,
) -> dict[str, object]:
    """Copy an Engine envelope and replace only its externally visible ID."""
    public_envelope = dict(envelope)
    engine_payload = envelope.get("payload")
    if not isinstance(engine_payload, dict):
        raise ValueError("interaction event envelope payload must be an object")
    public_payload = dict(engine_payload)
    public_payload["interactionId"] = baas_interaction_id
    if "id" in public_payload:
        public_payload["id"] = baas_interaction_id
    if "transitionId" in public_payload:
        public_payload["transitionId"] = baas_interaction_id
    public_envelope["payload"] = public_payload
    return public_envelope


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


class BotSessionError(Exception):
    """WebSocket 会话以 error 状态终止时抛出。

    当 agent/chat 事件回调收到 state=error 时，send_message 在
    chat_complete 后检查 state.state 并抛出此异常，使上游调用方
    （BaasBotService / executor）能将 bot_run 标记为 FAILED 而非 COMPLETED。
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
        interaction_service: BotInteractionService | None = None,
        a2a_mode: str = "off",
        sensitive_bypass_keys_loader: Callable[[], tuple[str, ...]] | None = None,
        proxypass_token_minter: Callable[[], Awaitable[str]] | None = None,
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
            sensitive_bypass_keys_loader: 敏感日志豁免片段读取函数
                （由连接池注入，返回解析后的片段元组）
        """
        self.uri = uri
        # 1b 迁移灰度开关（R16-10：DI 注入，默认 off=生产零变化）。
        if a2a_mode not in ("off", "grey", "on"):
            raise ValueError(f"invalid a2a_mode: {a2a_mode!r}")
        self._a2a_mode = a2a_mode
        self._transport: str = "v3-ws"
        self._v3_fallback = None  # B3: A2A 传输上附件请求的 v3 重路由
        # R7-F4：fallback 的检查/建连/发布互斥——并发首发都看到 None 会各
        # 建一条连接（身份守卫吞首连断连、末连断连误杀全部归属 run）。
        self._v3_fallback_lock = asyncio.Lock()
        # R10：正在分派 v3 fallback 帧的来源标记——归属释放只认 fallback
        # 帧（A2A 主连接与 fallback 共用同一套编排回调，不标记来源会让
        # A2A run 的终态参与 fallback 归属匹配而误清在途附件 run）。
        self._on_v3_fallback_frame = False
        # B3（1c）→ P1-4：session→传输归属表——附件请求 reroute 到 v3 后，
        # 该 session 的 chat_abort 与 HITL interaction_resolve 都必须随 run
        # 走 v3 专用连接（A2A kernel 注册表里没有该 run；正常 A2A session
        # 的答复仍走 A2A 客户端 CancelTask/ResolveInteraction 转发）。
        # 值为归属标签 "v3"；send 时记、收口清、off/v3 模式永不写。
        self._v3_routed_sessions: dict[str, str] = {}
        self.headers = headers or {}
        self.client_id = client_id or f"client-{uuid.uuid4().hex[:8]}"
        self.client_version = client_version
        self.verbose = verbose

        self._max_concurrent_sessions = max_concurrent_sessions
        self._session_key_timeout = session_key_timeout
        self._max_retries = max_retries
        self._retry_base_backoff = retry_base_backoff
        self._ignore_case = ignore_case
        self._interaction_service = interaction_service
        self._sensitive_bypass_keys_loader = sensitive_bypass_keys_loader
        self._proxypass_token_minter = proxypass_token_minter
        self._interaction_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

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
        """是否有活跃占据（供连接池过期清理/排空判断）。

        R6-1：除等待中的编排会话外，A2A 后台 body task（wait_result=
        False 的非等待发送）也算——sweep 不得把仍在服务真实 run 的连
        接当空闲收口。

        R7-F2：v3 fallback 上的附件后台 run 同样占据本连接的服务面
        （归属标记随 run 存活，与 A2A 的 has_running_runs 同口径）。
        """
        if len(self._active_sessions) > 0:
            return True
        if bool(getattr(self._client, "has_running_runs", False)):
            return True
        return bool(self._v3_routed_sessions)

    def tracks_session(self, session_key: str) -> bool:
        """该连接是否持有此 session 的在途运行——R4-1/R6-1/R7-F2 归属查询。

        三信号：①编排层 _sessions（调用方**等待期间**——等待中取消
        落在等待者上永远正确）；②底层 A2A run 在途（**run 执行期**——
        wait_result=False 的 ACK 返回后 _sessions 即被 pop，但 body
        task 仍在消费流，CancelTask 的 engine_task_id 记录还活着）；
        ③v3 fallback 归属标记（**附件 run 执行期**——B3 reroute 的 run
        建在 v3 专用连接上，标记由后台终态帧释放，不随等待方收口）。
        只看①会把"非等待发送后取消"退回普通负载分配，命中无记录的
        连接 → RUN_NOT_TRACKED 永不发 CancelTask。
        """
        if session_key in self._sessions:
            return True
        if session_key in self._v3_routed_sessions:
            return True
        inner_tracks = getattr(self._client, "tracks_session", None)
        return bool(inner_tracks and inner_tracks(session_key))

    @property
    def active_session_count(self) -> int:
        """当前活跃会话数（供连接池负载均衡使用）。"""
        return len(self._active_sessions)

    def refresh_outbound_auth(self, headers: dict[str, str] | None) -> None:
        """转发池层逐消息带来的最新鉴权头（P0：120s JWT 过期正修）。

        外层 headers 快照同步保存（评审 P2 场景 1）：v3 附件 fallback
        的懒建/重建握手（_ensure_v3_fallback）读的是外层快照——不同步
        则附件 WS 携带过期 token。空 token（Local 平台）不覆盖。
        duck-typed 转发内部 A2A 子客户端；v3 WS 握手早已完成、无请求
        级鉴权，无此方法即跳过；未连接（_client=None）同样跳过。
        """
        token = (headers or {}).get("x-proxypass-token", "")
        if token:
            self.headers["x-proxypass-token"] = token
        refresh = getattr(self._client, "refresh_proxypass_token", None)
        if refresh is not None:
            refresh(headers)

    async def connect(self) -> dict[str, Any]:
        """连接到服务器

        Returns:
            握手响应
        """
        if self._client is not None:
            raise RuntimeError("Already connected")

        self._closed_intentionally = False
        self._disconnect_event.clear()

        # ── 1b G1 灰度工厂（默认 off 时代码路径与迁移前逐字节一致）：
        # off → 原 BotWebSocketClient 构造；grey/on → A2A 能力探测，失败
        # 按模式分流（grey 回退 v3 / on 抛错）。handlers 注册与统一 connect
        # 段保持原样——见下方 hello 分支。──
        _client = None
        if self._a2a_mode in ("grey", "on"):
            a2a_base = _a2a_base_url_from_ws(self.uri)
            if a2a_base:
                from ._bot_a2a_client import A2aUnavailableError, BotA2aClient

                try:
                    candidate = BotA2aClient(base_url=a2a_base, headers=self.headers)
                    await candidate.connect()  # card GET 能力探测
                    _client = candidate
                except A2aUnavailableError as e:
                    if self._a2a_mode == "on":
                        raise
                    logger.info("[a2a-gate] probing failed (%s) — v3", e)
        if _client is None:
            _client = BotWebSocketClient(
                uri=self.uri,
                client_id=self.client_id,
                client_version=self.client_version,
                headers=self.headers,
            )

        _client.on_event("chat", self._on_chat)
        _client.on_event("agent", self._on_agent)
        _client.on_event("interaction.requested", self._on_interaction_requested)
        _client.on_event("interaction.resolved", self._on_interaction_resolved)
        _client.on_event("mode_transition.resolved", self._on_mode_transition_resolved)
        _client.on_event("error", self._on_error)
        _client.on_event("*", self._log_event)
        _client.on_disconnect(self._on_disconnect)

        if self.verbose:
            logger.info("Connecting...")

        # 直接 await 异步连接，不再需要 run_in_executor。
        # A2A 候选客户端在能力探测时已完成 connect（R16-4：不再二次连接，
        # hello 从 server_info 取）；v3 语义原样保留。
        self._transport = (
            "a2a" if _client.__class__.__name__ == "BotA2aClient" else "v3-ws"
        )
        hello: dict[str, Any] | None = None
        try:
            if self._transport == "a2a":
                hello = _client.server_info
            else:
                hello = await _client.connect()
        except Exception as e:
            logger.error(f"Connect failed: {e}")
            raise e
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

            # R7-F2/R8：归属释放不在等待侧做生命周期判定——唯一在此清理的情形是
            # "run 从未开始"（chat_send 被引擎拒）。其余任何等待方离开方式
            # （ACK 返回/超时/取消/流式提前关）run 都可能仍在 v3 连接上执行，
            # 标记由终态处理（_release_v3_ownership_if_terminal）、断连、
            # close 释放。R7 按"枚举退出方式保标记"的方案在 inject 帧/
            # CancelledError/终态先于恢复到达三个缝上全部漏判，R8 收敛于此。
            send_failed = False
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
                    send_failed = True
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
                # B3（1c）/R7-F2/R8：只有 run 从未开始才在等待侧释放归属
                if send_failed:
                    self._v3_routed_sessions.pop(session_key, None)

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
        assert self._client is not None
        # B3（1c）：附件请求 reroute 过 v3 的 session——run 在 v3 专用
        # 连接上，abort 随 run 走 v3（A2A 客户端没有该 run 的归属）；
        # 正常 session 的 abort 走 A2A 客户端 CancelTask 转发。
        # off/v3 模式（transport != "a2a"）永远直连 self._client，
        # 路由表不参与——v3 原路径不动。
        if (
            self._transport == "a2a"
            and session_key in self._v3_routed_sessions
            and self._v3_fallback is not None
        ):
            return await self._v3_fallback.chat_abort(
                session_key=session_key,
                run_id=run_id,
            )
        return await self._client.chat_abort(
            session_key=session_key,
            run_id=run_id,
        )

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

        # R9-F1：出口旗标先于外层 try 绑定——请求通过入口连接检查后可能
        # 在信号量/同 session 排队期间断连，恢复执行到内层连接复查时抛
        # NotConnectedError，finally 必须读得到已初始化的旗标，否则
        # UnboundLocalError 会吞掉真实断连原因。
        stream_send_failed = False
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
                    stream_send_failed = True
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
                # B3（1c）/R7-F2/R8：只有 run 从未开始才在等待侧释放归属
                if stream_send_failed:
                    self._v3_routed_sessions.pop(session_key, None)

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
            assert self._client is not None
            # B3（16-5）双入口：附件经 v3 fallback 重路由（与发送入口同语义）
            if attachments and self._transport == "a2a":
                target = await self._ensure_v3_fallback()
            else:
                target = self._client
            inject_result = await target.chat_inject(
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

    async def _ensure_v3_fallback(self):
        """B3（16-5）：A2A 连接上的附件请求→懒建 v3 专用客户端。

        handlers 从当前传输桥面复刻双注册（两个客户端 duck 面等义——同一套
        编排回调，会话收流一致性由 payload 形状契约保证）；on_disconnect 挂
        **专用隔离处理器**（R17-5 的会话归属隔离裁决：只终结归属本连接的
        会话，全局 _on_disconnect 会误杀 A2A 侧正常运行的请求）。复用前先
        查 connected——死连接失效收口后重建，不把坏连接发给下一个附件请求。

        R7-F4：检查/建连/发布全程持锁（含死连接失效）——并发首发只建一条
        连接；锁内重查使后来者直接复用，不再落到"各自 connect、后者覆盖
        前者"（那条路上身份守卫会吞掉首连断连、末连断连误杀全部归属）。
        """
        # 单次初始化互斥：锁内重查（第一发布前的并发等待者都能看到成品）
        async with self._v3_fallback_lock:
            if self._v3_fallback is not None:
                if self._v3_fallback.connected:
                    return self._v3_fallback
                # 死连接不复用：就地失效（身份守卫保证旧连接迟到的断连通知
                # 不会误伤后续重建的新连接），归属路由表随之收口——这些
                # 会话的 run 已随连接死亡，重路由语义不得残留。
                self._v3_fallback = None
                self._v3_routed_sessions.clear()
            # 调用期才解析（保持懒建语义；测试面经此注入替身）
            from ._bot_websocket_client import BotWebSocketClient

            client = BotWebSocketClient(
                uri=self.uri,
                client_id=self.client_id,
                client_version=self.client_version,
                headers=self.headers,
            )
            await client.connect()
            # R10：桥注册一律走来源包装——释放锚点靠 _on_v3_fallback_frame
            # 区分"帧来自 fallback"，A2A 主连接同名 handler 不带标记。
            for event_name, handler in self._event_bridge_snapshot().items():
                client.on_event(event_name, self._v3_scoped_bridge(handler))
            client.on_disconnect(self._v3_fallback_disconnect_handler(client))
            self._v3_fallback = client
            return client

    def _v3_scoped_bridge(self, handler: Callable) -> Callable:
        """R10：fallback 桥面来源包装——标记"正在分派 fallback 帧"。

        归属释放只认带来源标记的帧：A2A 主连接与 fallback 双桥注册共用
        同一套编排回调，A2A run（独立归属体系）的终态若参与 fallback
        归属匹配，裸 key 间的包含关系（chat-1 ⊂ chat-10）会误中前缀
        session 而清掉在途附件 run 的归属。

        语义前提：bridged handler 全部同步（_on_chat/_on_agent/_on_error/
        _log_event 均为 sync def）——标记的置位/复位绑定外层调用栈。若未来
        有返回协程的 handler 加入桥面，须改用 contextvar 承载标记。
        """

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self._on_v3_fallback_frame = True
            try:
                return handler(*args, **kwargs)
            finally:
                self._on_v3_fallback_frame = False

        return wrapped

    def _v3_fallback_disconnect_handler(self, client: Any) -> Callable[..., Any]:
        """R17-5/R3-2：fallback 断连的隔离收口处理器。

        与全局 _on_disconnect 的区别（也是它存在的理由）：只终结经
        _v3_routed_sessions 登记的、归属本 fallback 连接的会话——A2A 主
        连接上的在途请求一概不受影响。被终结的等待方收到明确 error
        （复用 _handle_terminal_error 的两路收口：非流式 chat_complete
        置位 + 流式 error chunk），不再悬挂等自身超时。

        身份守卫：处理器捕获构造时的 client 实例；旧连接迟到的断连通知
        （重建后 recv_loop 才走到 finally）直接忽略，不清新连接、不终结
        新连接的会话。已 ACK 的请求**不自动重发**——重复执行风险由显式
        失败交还调用方决策。
        """

        def handler(event_name: str, payload: dict[str, Any]) -> None:
            if self._v3_fallback is not client:
                return  # 旧连接的迟到通知：被守卫挡下
            routed = list(self._v3_routed_sessions.keys())
            self._v3_routed_sessions.clear()
            self._v3_fallback = None  # 失效收口；下次附件请求走重建
            logger.warning(
                "[v3-fallback] disconnected: terminating %d routed "
                "session(s) with explicit error",
                len(routed),
            )
            for session_key in routed:
                state = self._sessions.get(session_key)
                if state is not None and not state.chat_complete.is_set():
                    self._handle_terminal_error(
                        state,
                        session_key,
                        "fallback connection lost; run outcome unknown",
                        "fallback_disconnect",
                    )

        return handler

    def _event_bridge_snapshot(self) -> dict:
        """当前传输的已注册 handlers 快照（B3 双注册用）。"""
        source = self._client
        if source is None:
            return {}
        return dict(getattr(source, "_event_handlers", None) or {})

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
        if self._v3_fallback is not None:
            try:
                await self._v3_fallback.close()
            except Exception:
                logger.exception("v3 fallback close failed")
            self._v3_fallback = None
        self._v3_routed_sessions.clear()  # B3（1c）：归属路由表随断连收口

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

        WS client 只返回原始 result dict（纯数据边界）。本方法是**唯一的**
        ok 判断点、日志点和异常构造点：
        1. 打印完整 ``result``（debug 级别，成功/失败都打）；
        2. ``ok=False`` 时：打 error 日志 + 从 result 构造 ``ChatRequestError`` 并抛出；
        3. ``ok=True`` 时：打 info 日志 + 返回 result。
        """
        assert self._client is not None
        logger.info(
            "[%s] Sending: session_key=%s, timeout=%s",
            caller_label,
            session_key,
            timeout,
        )
        # ── B3（16-5）请求级特性门：A2A 传输上的附件请求重路由到 v3 专用
        # 连接——不静默丢参、不硬错。v3 fallback 懒创建一次（复用前查
        # connected，死连接就地失效重建）；事件 handlers 双注册（同一套
        # 编排回调）；on_disconnect 挂专用隔离处理器（R17-5/PR #2743 R3-2：
        # 只终结归属 fallback 的会话——全局断连回调会误杀 A2A 侧正常运行
        # 中的请求）。close() 一并收口。
        if attachments and self._transport == "a2a":
            target = await self._ensure_v3_fallback()
            # B3（1c/P1-4）：记归属——该 session 在 v3 专用连接上开了 run，
            # 后续 chat_abort 与 interaction_resolve 都随 run 走 v3，语义不丢。
            self._v3_routed_sessions[session_key] = "v3"
        else:
            target = self._client
            self._v3_routed_sessions.pop(session_key, None)
        send_result = await target.chat_send(
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

    def _get_session(self, session_key: str) -> SessionState | None:
        """获取指定 sessionKey 的状态（支持模糊匹配）。"""
        result = self._session_matcher.find(session_key)
        return result.state if result else None

    @staticmethod
    def _emit_stream_chunk(state: SessionState, chunk: StreamChunk) -> None:
        if state.stream_queue is not None:
            state.stream_queue.put_nowait(chunk)

    @staticmethod
    def _handle_terminal_error(
        state: SessionState,
        session_key: str,
        error_msg: str,
        source: str,
        error_code: str = "",
    ) -> None:
        msg = error_msg or f"{source} error"
        if error_code:
            msg = f"{error_code} - {msg}"
        logger.warning("[%s] error: sessionKey=%s, errMsg=%s", source, session_key, msg)
        state.state = "error"
        state.error_message = msg
        state.chat_complete.set()
        AsyncChatClient._emit_stream_chunk(
            state, StreamChunk(type="error", content=msg)
        )

    def _release_v3_ownership_if_terminal(
        self, session_key: str, chat_state: Any, payload: dict[str, Any]
    ) -> None:
        """R7-F2/R8/R9：run 终态统一释放 v3 fallback 归属标记。

        生命周期唯一事实源=引擎侧 run 结束。与等待面彻底解耦：
        等待方可能早已返回（wait_result=False / 超时）、可能仍在等
        （终态先于 ACK 恢复到达，R8-F3）、可能被中途取消（R8-F2）——
        本方法只看帧本身，不枚举等待方形态，也不依赖 session state
        是否已被 pop。

        R10：只认 v3 fallback 来源帧（_v3_scoped_bridge 置位）——归属
        标记只描述 fallback 连接上的 run；A2A 主连接借用同一套回调，其
        终态（各自持有独立归属记录）不得参与本表匹配，否则裸 key 包含
        关系（chat-1 ⊂ chat-10）会误清在途附件 run。

        R9-F2：帧上的 sessionKey 是引擎侧装饰形态（前后缀/大小写），
        与发送时的登记键可能不同——归并沿用 SessionKeyMatcher 同套
        分发语义（精确→contains），不能精确查找把模糊匹配语义拆了。

        注入帧不算主 run 终态（注入前后主 run 仍在执行，R8-F1）：
        沿用正常 final 分支的注入排除口径（runId inject- 前缀 /
        stopReason==inject）。无标记/非 a2a 时为 no-op（off 路径零变化）。
        """
        if not self._on_v3_fallback_frame:
            return  # R10：非 fallback 来源帧不参与本表清理
        if chat_state not in ("final", "error"):
            return
        run_id = payload.get("runId") or payload.get("run_id")
        if isinstance(run_id, str) and run_id.startswith("inject-"):
            return
        if payload.get("stopReason") == "inject":
            return
        # R9-F2：装饰 key → 登记键归并（state 已删的后台态也要能匹配，
        # 独立于 _sessions 的 matcher，直接对归属表跑同语义查找）
        reg_key = session_key
        if reg_key not in self._v3_routed_sessions:
            matched = SessionKeyMatcher(
                self._v3_routed_sessions, ignore_case=self._ignore_case
            ).find(session_key)
            if matched is None:
                return
            reg_key = matched.key
        self._v3_routed_sessions.pop(reg_key, None)
        logger.debug(
            "[chat] v3 routed run terminal: sessionKey=%s state=%s，归属释放",
            session_key,
            chat_state,
        )

    @_with_session_trace("_on_chat")
    def _on_chat(
        self,
        payload: dict[str, Any],
        *,
        session_key: str,
        state: SessionState | None,
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
        contents = message.get("content", [])

        # 提取文本内容
        text = ""
        for content in contents:
            if content.get("text") is not None:
                text = content.get("text")
                break

        # R7-F2/R8：归属释放锚定真实终态——先于 state 判定执行，
        # state 有无（等待方是否已收口）不影响释放（R8-F3 的缝正在于此）。
        self._release_v3_ownership_if_terminal(session_key, chat_state, payload)

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
            self._handle_terminal_error(
                state,
                session_key,
                payload.get("errorMessage", ""),
                "chat",
                error_code=payload.get("errorCode", ""),
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
        state: SessionState | None,
    ) -> None:
        """内部 agent 事件处理器。

        根据 payload 中的 sessionKey 分发到对应的 _SessionState。

        由 @_with_session_trace 装饰器自动查找 state 并恢复 trace context，
        使日志的 traceid 能正确关联原始请求。
        """
        # R9-F3：agent final/error 是真实终态通道（_derive_event_name 可把
        # 无显式事件名的终态派发为 agent；WS 转发层遇终态直接结束，不保证
        # 再补 chat 终态）——释放接入在 state 判定之前，等待中与后台两态
        # 无差别。
        self._release_v3_ownership_if_terminal(
            session_key, payload.get("state", ""), payload
        )
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
                state,
                session_key,
                payload.get("errorMessage", ""),
                "agent",
                error_code=payload.get("errorCode", ""),
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

    @_with_session_trace("_on_interaction_requested")
    def _on_interaction_requested(
        self,
        payload: dict[str, object],
        *,
        session_key: str,
        state: SessionState | None,
    ) -> None:
        """Persist and expose a validated engine interaction request."""
        if self._interaction_service is None:
            logger.debug("interaction processing is disabled; skip requested event")
            return

        event = EngineInteractionRequestedEvent.from_payload(
            session_key=session_key,
            payload=payload,
        )
        result = self._interaction_service.record_requested(
            session_key=event.session_key,
            interaction_id=event.interaction_id,
            envelope=event.envelope,
            allowed_decisions=event.allowed_decisions,
            expires_at_ms=event.expires_at_ms,
        )
        if result.created and state is not None:
            self._emit_stream_chunk(
                state,
                StreamChunk(
                    type="interaction",
                    content="",
                    metadata={
                        "event": "interaction.requested",
                        "payload": _public_interaction_envelope(
                            event.envelope,
                            baas_interaction_id=result.baas_interaction_id,
                        ),
                    },
                ),
            )
            if state.stream_queue is not None and payload.get("kind") == "mode_switch":
                state.pending_mode_transition_ids.add(event.interaction_id)

        # Engine events normally arrive only on an active client. Keep persistence
        # and SSE delivery above, but do not start a dispatcher without an uplink.
        if self._client is None:
            return

        task_key = (event.session_key, event.interaction_id)
        if task_key in self._interaction_tasks:
            return
        task = asyncio.create_task(
            self._dispatch_interaction_answer(
                session_key=event.session_key,
                interaction_id=event.interaction_id,
                deadline_ms=event.expires_at_ms,
            )
        )
        self._interaction_tasks[task_key] = task
        task.add_done_callback(
            lambda completed, key=task_key: self._discard_interaction_task(
                key, completed
            )
        )

    @_with_session_trace("_on_interaction_resolved")
    def _on_interaction_resolved(
        self,
        payload: dict[str, object],
        *,
        session_key: str,
        state: SessionState | None,
    ) -> None:
        """Persist and expose one terminal interaction.resolved event."""
        if self._interaction_service is None:
            logger.debug("interaction processing is disabled; skip resolved event")
            return

        event = EngineInteractionResolvedEvent.from_payload(
            session_key=session_key,
            payload=payload,
        )
        result = self._interaction_service.mark_resolved(
            session_key=event.session_key,
            interaction_id=event.interaction_id,
            envelope=event.envelope,
        )
        if result is None or not result.applied:
            return

        if state is not None:
            self._emit_stream_chunk(
                state,
                StreamChunk(
                    type="interaction",
                    content="",
                    metadata={
                        "event": "interaction.resolved",
                        "payload": _public_interaction_envelope(
                            event.envelope,
                            baas_interaction_id=result.baas_interaction_id,
                        ),
                    },
                ),
            )

    @_with_session_trace("_on_mode_transition_resolved")
    def _on_mode_transition_resolved(
        self,
        payload: dict[str, object],
        *,
        session_key: str,
        state: SessionState | None,
    ) -> None:
        """Persist a raw mode terminal event and expose it once to its stream."""
        if self._interaction_service is None:
            logger.debug(
                "interaction processing is disabled; skip mode transition event"
            )
            return

        event = EngineInteractionResolvedEvent.from_mode_transition_payload(
            session_key=session_key,
            payload=payload,
        )
        result = self._interaction_service.mark_resolved(
            session_key=event.session_key,
            interaction_id=event.interaction_id,
            envelope=event.envelope,
        )
        if (
            result is None
            or state is None
            or event.interaction_id not in state.pending_mode_transition_ids
        ):
            return

        state.pending_mode_transition_ids.remove(event.interaction_id)
        self._emit_stream_chunk(
            state,
            StreamChunk(
                type="interaction",
                content="",
                metadata={
                    "event": "mode_transition.resolved",
                    "payload": _public_interaction_envelope(
                        event.envelope,
                        baas_interaction_id=result.baas_interaction_id,
                    ),
                },
            ),
        )

    def _discard_interaction_task(
        self,
        key: tuple[str, str],
        completed: asyncio.Task[None],
    ) -> None:
        if self._interaction_tasks.get(key) is completed:
            self._interaction_tasks.pop(key, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.error(
                "[interaction] dispatch task failed: sessionKey=%s interactionId=%s",
                key[0],
                key[1],
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _dispatch_interaction_answer(
        self,
        *,
        session_key: str,
        interaction_id: str,
        deadline_ms: int | None,
    ) -> None:
        """Poll the DB, claim one queued answer, and send it to the engine."""
        interaction_service = self._interaction_service
        if interaction_service is None:
            return
        if deadline_ms is None:
            deadline_ms = int(time.time() * 1000) + 300_000

        while int(time.time() * 1000) <= deadline_ms:
            command = interaction_service.claim_for_dispatch(
                session_key=session_key,
                interaction_id=interaction_id,
            )
            if command is not None:
                # P1-4：答复按产生 interaction 的传输归属路由——B3 附件
                # reroute 的 run 在 v3 专用连接上，ResolveInteraction 必须发
                # 到那里（A2A kernel 注册表没有该 run，错发得 not_found、审批
                # 无法恢复）。正常 A2A session 走 A2A 客户端并带 session_key
                # （客户端用它取该 run 记录的 IAM 许可，P1-5）。off/v3 模式
                # 恒走 self._client 且调用签名原样——原路径不动。
                client = self._client
                resolve_kwargs: dict[str, Any] = {}
                if self._transport == "a2a":
                    if session_key in self._v3_routed_sessions:
                        client = self._v3_fallback
                    else:
                        resolve_kwargs["session_key"] = session_key
                if client is None:
                    interaction_service.mark_failed(
                        session_key=session_key,
                        interaction_id=interaction_id,
                        error="engine websocket is disconnected",
                    )
                    return

                # 评审 P2 场景 2：后台答复派发不经过 pool.get——发 RPC 前经注入
                # 的 minter 现签 proxypass token，不依赖下一条聊天消息带来的新
                # 鲜头（120s TTL 快照在此路径仍可能过期）。仅 A2A 直连分支需要
                # （v3 fallback 的 WS 握手鉴权，无请求级 token）。铸造失败回退
                # last-known token（警告日志），不得阻断派发。
                if client is self._client and self._proxypass_token_minter is not None:
                    try:
                        client.refresh_proxypass_token(
                            {
                                "x-proxypass-token": (
                                    await self._proxypass_token_minter()
                                )
                            }
                        )
                    except Exception:
                        logger.warning(
                            "[interaction] proxypass re-mint failed; "
                            "resolve continues on last-known token",
                            exc_info=True,
                        )
                try:
                    exchange = await client.interaction_resolve(
                        interaction_id=command.interaction_id,
                        resolution=command.resolution,
                        **resolve_kwargs,
                    )
                except Exception as exc:
                    logger.warning(
                        "[interaction] dispatch failed: sessionKey=%s interactionId=%s",
                        session_key,
                        interaction_id,
                        exc_info=True,
                    )
                    interaction_service.mark_failed(
                        session_key=session_key,
                        interaction_id=interaction_id,
                        error=str(exc),
                    )
                    return

                interaction_service.record_engine_exchange(
                    session_key=session_key,
                    interaction_id=interaction_id,
                    engine_req=exchange.request,
                    engine_res=exchange.response,
                )
                if not exchange.accepted:
                    interaction_service.mark_failed(
                        session_key=session_key,
                        interaction_id=interaction_id,
                        error=exchange.error_message
                        or "engine rejected interaction.resolve",
                    )
                elif command.resolution.kind == "mode_switch":
                    # The Engine resolves mode transitions through the RPC response
                    # and a compatibility agent stream; it does not emit the
                    # top-level interaction.resolved event used by other kinds.
                    interaction_service.mark_resolved(
                        session_key=session_key,
                        interaction_id=interaction_id,
                        envelope=exchange.response,
                    )
                return

            if not interaction_service.should_poll(
                session_key=session_key,
                interaction_id=interaction_id,
            ):
                return
            await asyncio.sleep(0.2)

        interaction_service.mark_expired(
            session_key=session_key,
            interaction_id=interaction_id,
        )

    @_with_session_trace("_on_error")
    def _on_error(
        self,
        payload: dict[str, Any],
        *,
        session_key: str,
        state: SessionState | None,
    ) -> None:
        """error 处理器
        Args:
            payload: 事件载荷
            session_key: 会话 key（由装饰器注入）
            state: 会话状态（由装饰器注入）
        """
        # R9-F3：error 通道同样终结 run——释放接入在 state 判定之前。
        self._release_v3_ownership_if_terminal(
            session_key, payload.get("state", ""), payload
        )
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
        state: SessionState | None,
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
        # session_key 命中豁免白名单时全量打印（不做敏感字段过滤）
        bypass_keys = (
            self._sensitive_bypass_keys_loader()
            if self._sensitive_bypass_keys_loader is not None
            else ()
        )
        if bypass_keys and any(k in session_key for k in bypass_keys):
            logger.info("[log_event] event=%s, payload=%s", event_name, payload)
            return

        # 含敏感内容的字段按事件类型过滤
        _sensitive_keys: dict[str, tuple[str, ...]] = {
            "chat": ("message", "deltaText", "delta"),
            "agent": ("data",),
        }
        if event_name in {
            "interaction.requested",
            "interaction.resolved",
            "mode_transition.resolved",
        }:
            metadata_keys = (
                "interactionId",
                "id",
                "runId",
                "sessionKey",
                "kind",
                "phase",
                "status",
                "toolCallId",
                "seq",
                "ts",
            )
            safe_payload = {
                key: payload[key] for key in metadata_keys if key in payload
            }
            logger.info("[log_event] event=%s, payload=%s", event_name, safe_payload)
        elif sensitive_keys := _sensitive_keys.get(event_name):
            safe_payload = {k: v for k, v in payload.items() if k not in sensitive_keys}
            logger.info("[log_event] event=%s, payload=%s", event_name, safe_payload)
        else:
            logger.info("[log_event] event=%s, payload=%s", event_name, payload)

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
        当底层 BotWebSocketClient 断连时被唤醒，以 exponential backoff
        尝试重建连接。重连成功后新请求可继续使用；在途会话已在
        _on_disconnect 中被标记为终态 error，不再等待自身 timeout。
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
                    new_client.on_event(
                        "interaction.requested", self._on_interaction_requested
                    )
                    new_client.on_event(
                        "interaction.resolved", self._on_interaction_resolved
                    )
                    new_client.on_event(
                        "mode_transition.resolved",
                        self._on_mode_transition_resolved,
                    )
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
