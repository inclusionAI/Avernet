"""BCN V3 Bot WebSocket 客户端。

职责：
- bot.connect 握手、重连、心跳；
- chat.send 立即 ACK，按 run/session 隔离生成最终产物；
- chat.inject 静默 ACK，chat.history 回放本地记录；
- chat.abort 精确取消并抑制该 run 的迟到输出；
- 节点超时后丢弃迟到结果，避免污染后续节点。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from hashlib import sha256
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from . import guard, protocol
from .brain import Answer, Brain, BrainUnavailable, SkipTask, build_brain, validate_answer
from .config import PlayerConfig
from .onboarding import OnboardingError, onboard_bot
from .protocol import (
    ALREADY_CONNECTED,
    INVALID_REQUEST,
    PROTOCOL_VERSION,
    RUN_NOT_ABORTABLE,
    RUN_NOT_FOUND,
    SCOPE_MISMATCH,
    Run,
    RunRegistry,
    chat_event_frame,
    decode_frame,
    error_response,
    now_ms,
    ok_response,
    parse_chat_send,
    request_frame,
)
from .store import EventLog, HistoryStore, SessionMemory, TokenStore
from .tasks import GameTask, TaskKind, TaskParseError, parse_task, vote_start_text

LOGGER = logging.getLogger("undercover_player")
_MAX_FRAME_BYTES = 8 * 1024 * 1024
_FRAME_HISTORY = 1000
# chat.abort 的本地确认预算：先等生成任务退出，再等最后一次 final 发送的锁。
# 两者都有上界，避免慢生成/慢发送阻塞接收循环。
_ABORT_GRACE_SECONDS = 5.0
_ABORT_LOCK_SECONDS = 2.0
_ABORT_RESPONSE_TIMEOUT = _ABORT_GRACE_SECONDS + _ABORT_LOCK_SECONDS + 1.0
# 会话级状态只保留有限数量的会话，并给长时间空闲会话设置 TTL；活跃 run 由清理逻辑保护。
_HISTORY_MAX_SESSIONS = 64
_SESSION_MEMORY_MAX_SESSIONS = 128
_SESSION_STATE_TTL_SECONDS = 6 * 60 * 60


def _abort_error(code: str, message: str) -> dict[str, object]:
    return {"ok": False, "code": code, "message": message}


def _abort_error_frame(frame_id: str, code: str, message: str) -> dict[str, object]:
    return error_response(frame_id, code, message, retryable=True)


class HandshakeError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BcnClient:
    def __init__(
        self,
        config: PlayerConfig,
        *,
        brain: Brain | None = None,
        token_store: TokenStore | None = None,
        event_log: EventLog | None = None,
        history: HistoryStore | None = None,
    ) -> None:
        self.config = config
        self.brain = brain if brain is not None else build_brain(config)
        self.token_store = token_store or TokenStore(config.token_file)
        self.event_log = event_log or EventLog(config.log_dir)
        self.history = history or HistoryStore(
            max_sessions=_HISTORY_MAX_SESSIONS,
            ttl_seconds=_SESSION_STATE_TTL_SECONDS,
        )
        self._memory = SessionMemory(
            max_sessions=_SESSION_MEMORY_MAX_SESSIONS,
            ttl_seconds=_SESSION_STATE_TTL_SECONDS,
        )
        self.registry = RunRegistry()
        self.connected = asyncio.Event()
        self.stopped = asyncio.Event()
        self.sent_frames: deque[dict[str, object]] = deque(maxlen=_FRAME_HISTORY)
        self.received_frames: deque[dict[str, object]] = deque(maxlen=_FRAME_HISTORY)
        self.last_error: str | None = None
        self._ws: Any = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._aux_tasks: set[asyncio.Task[None]] = set()
        self._send_lock = asyncio.Lock()
        self._stopping = False

    # ------------------------------------------------------------------ run loop

    async def run(self) -> None:
        """保持连接直到 stop()；断线按指数退避重连。"""

        backoff = self.config.reconnect_initial_seconds
        while not self._stopping:
            try:
                await self._connect_and_serve()
                backoff = self.config.reconnect_initial_seconds
            except asyncio.CancelledError:
                raise
            except HandshakeError as error:
                self.last_error = str(error)
                self._log("handshake_error", error=str(error), code=error.code)
                if error.code == ALREADY_CONNECTED:
                    self._log("already_connected_backoff")
                if not error.retryable:
                    raise
            except (ConnectionClosed, InvalidStatus, InvalidURI, OSError) as error:
                self.last_error = f"{type(error).__name__}: {error}"
                self._log("connection_error", error=self.last_error)
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
                self._log("unexpected_connection_error", error=self.last_error)
            finally:
                self.connected.clear()
                self._ws = None
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.config.reconnect_max_seconds)

    async def stop(self) -> None:
        self._stopping = True
        websocket = self._ws
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._aux_tasks):
            task.cancel()
        self._aux_tasks.clear()
        self.stopped.set()

    # -------------------------------------------------------------- connection

    async def _connect_and_serve(self) -> None:
        saved = self.token_store.load()
        token = None
        if saved and saved.get("bcs_url") == self.config.bcs_url and saved.get("bot_name") == self.config.bot_name:
            token = saved.get("token")
        async with connect(
            self.config.websocket_url(),
            max_size=_MAX_FRAME_BYTES,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            self._ws = websocket
            await self._handshake(websocket, token)
            self.connected.set()
            heartbeat = asyncio.create_task(self._heartbeat_loop(), name="bcn-heartbeat")
            try:
                async for raw in websocket:
                    await self._handle_raw(raw)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                self.connected.clear()
                self._cancel_all_runs("connection closed")

    async def _handshake(self, websocket: Any, token: str | None) -> None:
        request_id = f"connect-{uuid4().hex}"
        params: dict[str, object] = {"protocol_version": PROTOCOL_VERSION, "client_kind": "custom-engine"}
        if token:
            params["token"] = token
        await self._send(request_frame(request_id, "bot.connect", params), websocket=websocket)
        frame = await self._await_response(websocket, request_id)
        if not frame.get("ok"):
            error = frame.get("error")
            code = None
            message = "bot.connect rejected"
            if isinstance(error, dict):
                code = error.get("code") if isinstance(error.get("code"), str) else None
                message = str(error.get("message") or message)
            raise HandshakeError(message, code=code)
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            raise HandshakeError("bot.connect response has no payload", retryable=False)
        bot_uuid = payload.get("bot_uuid")
        new_token = payload.get("token")
        if not isinstance(bot_uuid, str) or not bot_uuid or not isinstance(new_token, str) or not new_token:
            raise HandshakeError("bot.connect response is missing identity", retryable=False)
        negotiated = payload.get("protocol_version")
        if negotiated != PROTOCOL_VERSION:
            raise HandshakeError(f"protocol version {negotiated!r} is not {PROTOCOL_VERSION}", retryable=False)
        capabilities = payload.get("capabilities")
        if isinstance(capabilities, dict):
            for name in ("unified_run_events", "canonical_session_id"):
                if capabilities.get(name) is not True:
                    raise HandshakeError(f"BCN capability {name} is not enabled", retryable=False)
        is_new = bool(payload.get("is_new"))
        if is_new:
            try:
                await asyncio.to_thread(onboard_bot, self.config, bot_uuid=bot_uuid, token=new_token)
            except OnboardingError as error:
                self._log("onboard_failed", status=error.status, unauthorized=error.unauthorized)
                raise HandshakeError(str(error), retryable=True) from error
        self.token_store.save(
            bcs_url=self.config.bcs_url,
            bot_uuid=bot_uuid,
            token=new_token,
            bot_name=self.config.bot_name,
        )
        self._log("connected", bot_uuid=bot_uuid, is_new=is_new, protocol_version=negotiated)

    async def _await_response(self, websocket: Any, frame_id: str) -> dict[str, object]:
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=15)
            frame = decode_frame(raw)
            if frame.get("type") == "res" and frame.get("id") == frame_id:
                return frame
            await self._handle_frame(frame)

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.heartbeat_seconds)
                await self._send(request_frame(f"status-{uuid4().hex}", "bot.status", {}))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._log("heartbeat_failed", error=type(error).__name__)

    # ---------------------------------------------------------------- dispatch

    async def _handle_raw(self, raw: str | bytes) -> None:
        try:
            frame = decode_frame(raw)
        except protocol.ProtocolError as error:
            self._log("invalid_frame", error=str(error))
            return
        await self._handle_frame(frame)

    async def _handle_frame(self, frame: dict[str, object]) -> None:
        self.received_frames.append(frame)
        frame_type = frame.get("type")
        if frame_type == "res":
            if not frame.get("ok"):
                self._log("response_error", frame_id=frame.get("id"), error=frame.get("error"))
            return
        if frame_type != "req":
            return
        frame_id = frame.get("id")
        method = frame.get("method")
        if not isinstance(frame_id, str) or not isinstance(method, str):
            return
        if method == "chat.send":
            await self._handle_chat_send(frame)
        elif method == "chat.inject":
            await self._send(ok_response(frame_id))
            self._log("chat_inject_acked", frame_id=frame_id)
        elif method == "chat.abort":
            await self._handle_chat_abort(frame)
        elif method == "chat.history":
            await self._handle_chat_history(frame)
        elif method == "bot.ping":
            await self._send(ok_response(frame_id))
        else:
            await self._send(error_response(frame_id, protocol.UNKNOWN_METHOD, f"unknown method: {method}"))

    async def _handle_chat_send(self, frame: dict[str, object]) -> None:
        frame_id = str(frame.get("id"))
        try:
            chat = parse_chat_send(frame)
        except protocol.ProtocolError as error:
            await self._send(error_response(frame_id, INVALID_REQUEST, str(error)))
            self._log("chat_send_rejected", frame_id=frame_id, error=str(error))
            return
        active = self.registry.get(chat.frame_id)
        if active is not None:
            if active.session_key != chat.session_key or active.session_id != chat.session_id:
                # run id 全局唯一；同一 id 跨 session 重放是协议冲突，不能当成幂等重发。
                await self._send(
                    error_response(
                        frame_id,
                        INVALID_REQUEST,
                        "run id is already active in another session",
                        retryable=False,
                    )
                )
                self._log("chat_send_scope_conflict", run_id=chat.frame_id, active_session=active.session_id)
                return
            await self._send(ok_response(chat.frame_id, {"run_id": chat.frame_id}))
            self._log("chat_send_duplicate", run_id=chat.frame_id)
            return
        tombstone_session = self.registry.tombstone_session(chat.frame_id)
        if tombstone_session is not None:
            if tombstone_session != chat.session_key:
                await self._send(
                    error_response(
                        frame_id,
                        INVALID_REQUEST,
                        "run id belongs to another session",
                        retryable=False,
                    )
                )
                self._log("chat_send_scope_conflict", run_id=chat.frame_id, tombstone_session=tombstone_session)
                return
            await self._send(ok_response(chat.frame_id, {"run_id": chat.frame_id}))
            self._log("chat_send_duplicate", run_id=chat.frame_id)
            return
        await self._send(ok_response(chat.frame_id, {"run_id": chat.frame_id}))
        deadline = None if chat.timeout_ms is None else now_ms() + chat.timeout_ms
        run = Run(
            run_id=chat.frame_id,
            session_key=chat.session_key,
            session_id=chat.session_id,
            deadline_ms=deadline,
            chat=chat,
        )
        if not self.registry.add(run):
            return
        task = asyncio.create_task(self._process_run(run), name=f"run-{chat.frame_id}")
        run.task = task
        self._tasks[run.run_id] = task
        task.add_done_callback(lambda _task, run_id=run.run_id: self._tasks.pop(run_id, None))

    async def _handle_chat_abort(self, frame: dict[str, object]) -> None:
        frame_id = str(frame.get("id"))
        params = frame.get("params")
        if not isinstance(params, dict):
            await self._send(error_response(frame_id, INVALID_REQUEST, "chat.abort params must be an object"))
            return
        session_key = params.get("session_key")
        run_id = params.get("run_id")
        if not isinstance(session_key, str) or not session_key:
            await self._send(error_response(frame_id, INVALID_REQUEST, "chat.abort requires session_key"))
            return
        if not isinstance(run_id, str) or not run_id:
            await self._send(error_response(frame_id, INVALID_REQUEST, "chat.abort requires exact run_id"))
            return
        run = self.registry.get(run_id)
        if run is None:
            tombstone_session = self.registry.tombstone_session(run_id)
            if tombstone_session is None:
                await self._send(error_response(frame_id, RUN_NOT_FOUND, "unknown run_id", retryable=False))
                self._log("abort_unknown", run_id=run_id)
                return
            if tombstone_session != session_key:
                await self._send(
                    error_response(frame_id, SCOPE_MISMATCH, "run_id does not belong to session_key")
                )
                self._log("abort_scope_mismatch", run_id=run_id)
                return
            await self._send(ok_response(frame_id, {"aborted": False, "aborted_run_ids": []}))
            self._log("abort_terminal", run_id=run_id)
            return
        if run.session_key != session_key:
            await self._send(error_response(frame_id, SCOPE_MISMATCH, "run_id does not belong to session_key"))
            self._log("abort_scope_mismatch", run_id=run_id)
            return
        # 先同步置 aborted：即使生成任务忽略 CancelledError，之后也不能再发 final。
        run.aborted = True
        if run.abort_future is None:
            run.abort_future = asyncio.get_running_loop().create_future()
            task = run.task or self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
            self._spawn(self._finalize_abort(run, task))
            self._log("abort_requested", run_id=run_id, session_id=run.session_id)
        # 本地确认（任务退出 + final 发送锁排空）由后台完成；接收循环继续处理其它帧。
        self._spawn(self._respond_to_abort(frame_id, run))

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._aux_tasks.add(task)
        task.add_done_callback(self._aux_done)

    def _aux_done(self, task: asyncio.Task[None]) -> None:
        self._aux_tasks.discard(task)
        with contextlib.suppress(BaseException):
            task.exception()

    async def _finalize_abort(self, run: Run, task: asyncio.Task[None] | None) -> None:
        """等待生成任务真正退出，并排空可能正在发送的 final，再给出本地结论。"""

        try:
            if task is not None:
                done, _pending = await asyncio.wait({task}, timeout=_ABORT_GRACE_SECONDS)
                if not done:
                    self._log("abort_timeout", run_id=run.run_id, grace_seconds=_ABORT_GRACE_SECONDS)
                    self._resolve_abort(
                        run,
                        _abort_error(RUN_NOT_ABORTABLE, "generation did not stop within the abort grace period"),
                    )
                    return
                with contextlib.suppress(BaseException):
                    task.exception()
            try:
                async with asyncio.timeout(_ABORT_LOCK_SECONDS):
                    async with run.terminal_lock:
                        pass
            except TimeoutError:
                self._log("abort_send_lock_timeout", run_id=run.run_id)
                self._resolve_abort(
                    run,
                    _abort_error(RUN_NOT_ABORTABLE, "final delivery is still in flight"),
                )
                return
            if run.terminal and run.terminal_event_sent:
                # final 已经发出，取消请求按协议幂等成功，但不能谎称这次取消生效。
                result = {"ok": True, "payload": {"aborted": False, "aborted_run_ids": []}}
            else:
                self.registry.mark_terminal(run, aborted=True)
                result = {"ok": True, "payload": {"aborted": True, "aborted_run_ids": [run.run_id]}}
                self._log("aborted", run_id=run.run_id, session_id=run.session_id)
            self._resolve_abort(run, result)
        except Exception as error:  # noqa: BLE001 - abort 清理失败必须显式返回协议错误
            self._log("abort_failed", run_id=run.run_id, error=f"{type(error).__name__}: {error}")
            self._resolve_abort(run, _abort_error(RUN_NOT_ABORTABLE, "failed to confirm cancellation"))

    def _resolve_abort(self, run: Run, result: dict[str, object]) -> None:
        future = run.abort_future
        if future is not None and not future.done():
            future.set_result(result)

    async def _respond_to_abort(self, frame_id: str, run: Run) -> None:
        future = run.abort_future
        if future is None:
            await self._send(_abort_error_frame(frame_id, RUN_NOT_ABORTABLE, "abort state is missing"))
            return
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=_ABORT_RESPONSE_TIMEOUT)
        except TimeoutError:
            # 生成器始终不退出：不能谎称已取消，保持 aborted 标记以继续抑制迟到 final。
            await self._send(
                _abort_error_frame(
                    frame_id,
                    RUN_NOT_ABORTABLE,
                    "generation did not stop before the abort response deadline",
                )
            )
            self._log("abort_response_timeout", run_id=run.run_id)
            return
        if result.get("ok") is True:
            payload = result.get("payload")
            await self._send(ok_response(frame_id, payload if isinstance(payload, dict) else {}))
        else:
            await self._send(
                _abort_error_frame(
                    frame_id,
                    str(result.get("code") or RUN_NOT_ABORTABLE),
                    str(result.get("message") or "abort failed"),
                )
            )

    async def _handle_chat_history(self, frame: dict[str, object]) -> None:
        frame_id = str(frame.get("id"))
        params = frame.get("params")
        session_key = params.get("session_key") if isinstance(params, dict) else None
        if not isinstance(session_key, str) or not session_key:
            await self._send(error_response(frame_id, INVALID_REQUEST, "chat.history requires session_key"))
            return
        limit = params.get("limit") if isinstance(params, dict) else None
        active_sessions = self._active_session_keys()
        messages = self.history.messages(session_key, active_session_keys=active_sessions)
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            messages = messages[-limit:]
        await self._send(
            ok_response(
                frame_id,
                {
                    "session_key": session_key,
                    "session_id": self.history.session_id(session_key) or "",
                    "messages": messages,
                },
            )
        )

    # ------------------------------------------------------------- generation

    async def _process_run(self, run: Run) -> None:
        started_ms = now_ms()
        try:
            known = self._memory.get(run.session_id, active_session_keys=self._active_session_keys())
            known_seat, known_word = known if known is not None else (None, None)
            try:
                task = parse_task(run.chat.text, known_word=known_word, known_seat=known_seat)
            except TaskParseError:
                task = GameTask(
                    kind=TaskKind.UNKNOWN,
                    node_id="",
                    instruction=run.chat.text,
                    raw_text=run.chat.text,
                )
            if task.seat is not None or task.word is not None:
                self._memory.remember(
                    run.session_id,
                    task.seat if task.seat is not None else known_seat,
                    task.word if task.word is not None else known_word,
                    active_session_keys=self._active_session_keys(),
                )
            self._log(
                "task_received",
                run_id=run.run_id,
                session_id=run.session_id,
                kind=task.kind.value,
                node_id=task.node_id,
                seat=task.seat,
                round=task.round,
                is_pk=task.is_pk,
                deadline_ms=run.deadline_ms,
            )
            if task.kind == TaskKind.UNKNOWN:
                self._log("task_unrecognized", run_id=run.run_id, session_id=run.session_id, node_id=task.node_id)
                if "[State Machine Task]" in run.chat.text:
                    # 游戏协作节点必须明确失败，绝不能给主持人一个看似成功的 final。
                    await self._send_error(
                        run,
                        "UNRECOGNIZED_NODE",
                        "state machine node task could not be recognized",
                    )
                else:
                    # 普通聊天没有游戏节点语义；给一句保守的拒绝，不伪装成任务完成。
                    text = "我只处理谁是卧底节点任务。"
                    sent = await self._send_final(run, text)
                    if sent:
                        self.history.append(
                            run.session_key,
                            text,
                            session_id=run.session_id,
                            active_session_keys=self._active_session_keys(),
                        )
                        self._log(
                            "task_completed",
                            run_id=run.run_id,
                            session_id=run.session_id,
                            kind="unknown",
                            source="rules",
                            chars=len(text),
                            word_hash=None,
                            degraded=True,
                            duration_ms=max(0, now_ms() - started_ms),
                        )
                        self._log("chat_unrecognized", run_id=run.run_id, session_id=run.session_id, chars=len(text))
                return
            if task.kind == TaskKind.ONBOARDING:
                self.registry.mark_terminal(run)
                return
            try:
                answer = await self._answer(task, run)
            except BrainUnavailable as error:
                emergency = self._emergency_text(task)
                if emergency is None or validate_answer(task, emergency) is not None:
                    self._log("brain_unavailable", run_id=run.run_id, error=str(error))
                    await self._send_error(run, "ANSWER_UNAVAILABLE", "cannot produce a valid game answer")
                    return
                answer = Answer(emergency, "rules", f"brain unavailable: {error}")
                self._log("brain_unavailable", run_id=run.run_id, error=str(error))
            text = guard.normalize_sentence(answer.text)
            issue = validate_answer(task, text)
            if issue is not None:
                emergency = self._emergency_text(task)
                if emergency is not None and validate_answer(task, emergency) is None:
                    text, answer = emergency, Answer(emergency, "rules", f"guard fallback: {issue}")
                    issue = None
            if issue is not None:
                self._log("answer_invalid", run_id=run.run_id, kind=task.kind.value, issue=issue)
                await self._send_error(run, "INVALID_ANSWER", "game answer failed validation")
                return
            sent = await self._send_final(run, text)
            if not sent:
                return
            self.history.append(
                run.session_key,
                text,
                session_id=run.session_id,
                active_session_keys=self._active_session_keys(),
            )
            self._log(
                "task_completed",
                run_id=run.run_id,
                session_id=run.session_id,
                kind=task.kind.value,
                source=answer.source,
                chars=len(text),
                word_hash=self._word_hash(task.word),
                degraded=answer.source != "llm",
                duration_ms=max(0, now_ms() - started_ms),
            )
        except asyncio.CancelledError:
            self._log("task_cancelled", run_id=run.run_id)
            raise
        except SkipTask:
            self.registry.mark_terminal(run)
        except Exception as error:
            self._log("task_failed", run_id=run.run_id, error=f"{type(error).__name__}: {error}")
            await self._send_error(run, "TASK_FAILED", "player task failed")
        finally:
            if not run.terminal:
                self.registry.mark_terminal(run, aborted=run.aborted)
            self._prune_session_state()

    def _active_session_keys(self) -> set[str]:
        return {run.session_id for run in self.registry.active()}

    def _prune_session_state(self) -> None:
        active = self._active_session_keys()
        self._memory.prune(active)
        self.history.prune(active)

    async def _answer(self, task: GameTask, run: Run) -> Answer:
        try:
            return await asyncio.wait_for(self.brain.answer(task, run.run_id), timeout=self._answer_timeout(run))
        except TimeoutError as error:
            self._log("brain_timeout", run_id=run.run_id)
            raise BrainUnavailable("brain timeout") from error

    def _answer_timeout(self, run: Run) -> float:
        if run.deadline_ms is None:
            return self.config.llm_timeout_seconds + 10
        remaining = (run.deadline_ms - now_ms()) / 1000
        return max(0.5, min(self.config.llm_timeout_seconds + 10, remaining - 1.0))

    def _emergency_text(self, task: GameTask) -> str | None:
        """模型与规则都失败时的最后兜底；只保证硬约束，不保证文采。"""

        if task.kind == TaskKind.VOTE:
            candidates = [seat for seat in task.legal_targets if seat != task.seat]
            if candidates:
                return f"我投{candidates[0]}号"
            return "我弃权" if task.allow_abstain else "我投1号"
        if task.kind == TaskKind.EULOGY:
            seats = [seat for seat in task.candidate_seats if seat != task.seat]
            return f"我最不放心{seats[0] if seats else 1}号。"
        if task.kind == TaskKind.VOTE_START:
            return vote_start_text()
        if task.kind == TaskKind.SPEECH:
            return "嗯，不太好说。"
        return None

    async def _send_error(self, run: Run, error_code: str, error_message: str) -> bool:
        """发送明确的失败终态；游戏节点无法识别时使用，而不是编造成功 final。"""

        async with run.terminal_lock:
            if run.aborted or run.terminal:
                self._log("error_suppressed_aborted", run_id=run.run_id)
                return False
            if run.expired():
                self.registry.mark_terminal(run)
                self._log("error_suppressed_expired", run_id=run.run_id)
                return False
            seq = run.next_seq()
            frame = chat_event_frame(
                run_id=run.run_id,
                session_id=run.session_id,
                seq=seq,
                state="error",
                error_code=error_code,
                error_message=error_message,
            )
            await self._send(frame)
            run.terminal_event_sent = True
            self.registry.mark_terminal(run)
            self._log(
                "terminal_error_sent",
                run_id=run.run_id,
                session_id=run.session_id,
                seq=seq,
                error_code=error_code,
            )
            return True

    async def _send_final(self, run: Run, text: str) -> bool:
        """在 run 级锁内做最后一次 aborted/expired 检查并发送 final。

        返回 False 表示结果被抑制（取消或过期），调用方不得再写历史/标记完成。
        """

        async with run.terminal_lock:
            if run.aborted or run.terminal:
                self._log("result_dropped_aborted", run_id=run.run_id)
                return False
            if run.expired():
                self.registry.mark_terminal(run)
                self._log("result_dropped_expired", run_id=run.run_id, deadline_ms=run.deadline_ms)
                return False
            seq = run.next_seq()
            frame = chat_event_frame(
                run_id=run.run_id,
                session_id=run.session_id,
                seq=seq,
                state="final",
                content=text,
            )
            await self._send(frame)
            run.final_sent = True
            run.terminal_event_sent = True
            self.registry.mark_terminal(run, result=text)
            self._log("final_sent", run_id=run.run_id, session_id=run.session_id, seq=seq, chars=len(text))
            return True

    # ---------------------------------------------------------------- plumbing

    async def _send(self, frame: dict[str, object], *, websocket: Any = None) -> None:
        target = websocket if websocket is not None else self._ws
        if target is None:
            raise RuntimeError("BCN websocket is not connected")
        raw = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        async with self._send_lock:
            await target.send(raw)
        self.sent_frames.append(frame)

    def _cancel_all_runs(self, reason: str) -> None:
        for run in self.registry.active():
            self.registry.mark_terminal(run)
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._aux_tasks):
            task.cancel()
        self._tasks.clear()
        self._aux_tasks.clear()
        self._prune_session_state()
        self._log("runs_cancelled", reason=reason)

    def _word_hash(self, word: str | None) -> str | None:
        if not word:
            return None
        return sha256(word.encode("utf-8")).hexdigest()[:12]

    def _log(self, event: str, **fields: object) -> None:
        self.event_log.write(event, **fields)
        level = logging.WARNING if event.endswith(("failed", "error", "invalid")) else logging.INFO
        LOGGER.log(level, "%s %s", event, fields)
