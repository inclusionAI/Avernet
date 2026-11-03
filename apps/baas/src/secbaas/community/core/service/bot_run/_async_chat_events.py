"""Transport-neutral session events, streaming translation and interaction delivery.

This mixin preserves the chat facade's callback surface while keeping event
translation separate from request orchestration and concrete wire clients.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from secbaas.community.api.sse import StreamChunk
from secbaas.community.logger import get_logger
from secbaas.community.tracer import get_tracer_plugin

from ..bot_interaction import BotInteractionService
from ._async_chat_client_transport import AsyncChatTransportBase
from ._interaction_protocol import (
    EngineInteractionRequestedEvent,
    EngineInteractionResolvedEvent,
)
from ._session_key_matcher import SessionKeyMatcher
from ._session_state import SessionState

logger = get_logger("core-bot-run")


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

    适用于 _on_chat / _on_agent 等接收回调，这些回调在后台 Task
    中执行，无 active trace context。装饰器自动：
      1. 从 payload.sessionKey 查找 _SessionState（支持模糊匹配）
      2. 恢复 state 中保存的 trace context，使日志 traceid 关联原始请求
      3. 将 state 作为关键字参数传入被装饰方法
      4. 执行完毕后还原上下文
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(self: AsyncChatEvents, *args: Any) -> None:
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


class AsyncChatEvents:
    """Internal callback implementation; state is initialized by AsyncChatClient."""

    _sessions: dict[str, SessionState]
    _session_matcher: SessionKeyMatcher
    _ignore_case: bool
    _interaction_service: BotInteractionService | None
    _chat_transport: AsyncChatTransportBase
    _client: Any | None
    _interaction_tasks: dict[tuple[str, str], asyncio.Task[None]]
    _sensitive_bypass_keys_loader: Callable[[], tuple[str, ...]] | None
    verbose: bool

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
        AsyncChatEvents._emit_stream_chunk(
            state, StreamChunk(type="error", content=msg)
        )

    def _release_terminal_ownership(
        self, session_key: str, chat_state: Any, payload: dict[str, Any]
    ) -> None:
        """Delegate terminal ownership release to the applicable transport."""
        self._chat_transport.release_terminal_ownership(
            session_key=session_key,
            chat_state=chat_state,
            payload=payload,
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
        self._release_terminal_ownership(session_key, chat_state, payload)

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
        self._release_terminal_ownership(session_key, payload.get("state", ""), payload)
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

        def discard_interaction_task(completed: asyncio.Task[None]) -> None:
            self._discard_interaction_task(task_key, completed)

        task.add_done_callback(discard_interaction_task)

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
                # P1-4：答复按产生 interaction 的传输归属路由——B3 的
                # reroute run 必须回到其专用连接，错发只会得到 not_found
                # 并让审批无法恢复。交互分发把这些差异完全交给策略；
                # 调用方只拿到目标 client 和额外调用参数。
                client, resolve_kwargs = await self._chat_transport.interaction_target(
                    session_key=session_key
                )
                if client is None:
                    interaction_service.mark_failed(
                        session_key=session_key,
                        interaction_id=interaction_id,
                        error="engine websocket is disconnected",
                    )
                    return

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
        self._release_terminal_ownership(session_key, payload.get("state", ""), payload)
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
