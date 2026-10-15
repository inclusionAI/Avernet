"""BotA2aClient — BotWebSocketClient 的 A2A v1.0 传输实现（1b 灰度）。

R17 契约对齐基准（全部 cat 自真实代码）：
  - chat_send/chat_inject 签名：_bot_websocket_client.py:240-248/:281-288
  - ok 契约：_send_chat_request(:725-775) 统一判断 {ok, error{code,message,retryable}}
  - interaction_resolve 返回 EngineInteractionResolveExchange（真实
    dataclass——消费者 :1210-1218 属性访问 exchange.request/.response/.accepted/
    .error_message）；入参 InteractionResolution 完整 schema
    （api/bot_interaction/_models.py:12-31）
  - 事件七类 + StreamChunk.metadata.engine_frame 逃生舱（_async_chat_client.py:282-289）
R17 修正（本文件就是计划 B1 的落地实现——不再"计划代码 vs 实现代码"两张皮）：
  - L1 生命周期：chat_send 两段式（ack 立即返回 + per-request body task）
  - T1 终态单次；E1 错误二分；M1 无损（engine_frame + original_event 路由）
  - R17-7：metadata 与 WS 同构平铺；timeout_ms 经 metadata.timeoutMs
    上行 + HTTP 超时下行
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from secbaas.community.api.bot_interaction import InteractionResolution
from secbaas.community.logger import get_logger

from ._interaction_protocol import EngineInteractionResolveExchange

logger = get_logger("core-bot-run")

EventHandler = Callable[..., Any]

_TERMINAL_WIRE_STATES = (
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
)

# 1c：引擎能力卡上 CancelTask 服务能力的 extension 声明（与
# engine/.../a2a/card.py EXTENSION_URIS["cancel"] 同源——spec v1.0
# AgentCapabilities 无标准 cancellation 字段，能力以 extension 锁定）。
_CANCEL_EXT_URI = "https://avernet.local/a2a/ext/cancel/v1"


class A2aUnavailableError(RuntimeError):
    """目标不可用/非 JSONRPC 1.x——工厂回退 v3 的灰度门信号。"""


class _RunCtx:
    __slots__ = (
        "rid",
        "session_key",
        "final_text",
        "terminal_emitted",
        "task",
        "engine_task_id",
        "auth_token",
    )

    def __init__(self, rid: str, session_key: str):
        self.rid = rid
        self.session_key = session_key
        self.final_text: str | None = None
        self.terminal_emitted = False
        self.task: asyncio.Task | None = None
        # 1c：wire 上的引擎侧 run id（statusUpdate/artifactUpdate 帧的
        # taskId，kernel 生成）——CancelTask 转发的定位键；BaaS 自己的 rid
        # 只是 messageId/幂等键，引擎不认识。
        self.engine_task_id: str | None = None
        # P1-5：该次 chat_send 携带的调用方许可（X-IAM-Token gate 开启时
        # CancelTask/ResolveInteraction 都必须用它——proxypass 头不是许可）。
        self.auth_token: str | None = None


def _message_to_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(message)


def _flatten_metadata(
    chat_metadata: dict[str, str] | None, app_id: str | None, timeout_ms: int | None
) -> dict[str, Any]:
    """R17-7: 与 WS 同构——chat_metadata 平铺键原样进 metadata（不嵌套）。"""
    meta: dict[str, Any] = {}
    if chat_metadata:
        meta.update(chat_metadata)
    if app_id:
        meta["appId"] = app_id
    if timeout_ms:
        meta["timeoutMs"] = str(timeout_ms)
    return meta


class BotA2aClient:
    """传输兄弟——方法面与 _bot_websocket_client.py 的对应成员 duck 等义。"""

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict | None = None,
        connect_timeout: float = 10.0,
        http_transport_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.rpc_url = f"{self.base_url}/rpc"
        self.card_url = f"{self.base_url}/.well-known/agent-card.json"
        self.headers = dict(headers or {})
        self._connect_timeout = connect_timeout
        self._http_factory = http_transport_factory
        self._event_handlers: dict[str, EventHandler] = {}
        self._disconnect_callback: EventHandler | None = None
        self._connected = False
        self._server_info: dict[str, Any] | None = None
        self._runs: dict[str, _RunCtx] = {}
        self._session_runs: dict[str, str] = {}

    # ── 可测性：全方法唯一的 httpx 现场化出口 ──
    def _http(self, *, timeout: float | None) -> httpx.AsyncClient:
        if self._http_factory is not None:
            return self._http_factory()
        return httpx.AsyncClient(timeout=timeout, proxy=None)

    # ── 生命周期 ──
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_info(self) -> dict[str, Any] | None:
        return self._server_info

    @property
    def features(self) -> dict[str, Any] | None:
        return (self._server_info or {}).get("features")

    async def connect(self) -> dict[str, Any]:
        """能力探测：card GET → JSONRPC + 1.x 才可用（灰度门）。"""
        if self._connected:
            raise RuntimeError("Already connected")
        try:
            async with self._http(timeout=self._connect_timeout) as http:
                resp = await http.get(self.card_url, headers=self.headers)
                resp.raise_for_status()
                card = resp.json()
        except Exception as e:
            raise A2aUnavailableError(f"A2A capability probe failed: {e}") from e
        matched = any(
            (
                i.get("protocolBinding") == "JSONRPC"
                and str(i.get("protocolVersion", "")).startswith("1.")
            )
            for i in card.get("supportedInterfaces", [])
        )
        if not matched:
            raise A2aUnavailableError("no JSONRPC 1.x interface on card")
        self._server_info = {
            "type": "hello-ok",
            "protocol": 1,
            "server": {"version": card.get("version", ""), "card": card},
            "a2a": True,
        }
        self._connected = True
        return dict(self._server_info)

    def on_event(self, event_name: str, handler: EventHandler) -> None:
        self._event_handlers[event_name] = handler

    def on_disconnect(self, handler: EventHandler) -> None:
        self._disconnect_callback = handler

    async def _notify_disconnect(self) -> None:
        if self._disconnect_callback:
            try:
                self._disconnect_callback("disconnect", {})
            except Exception:
                logger.exception("BotA2aClient disconnect handler error")

    async def close(self) -> None:
        """L1：取消在途 body task（真实流持有者），再断连通知。"""
        self._connected = False
        for ctx in list(self._runs.values()):
            if ctx.task and not ctx.task.done():
                ctx.task.cancel()
        self._runs.clear()
        self._session_runs.clear()
        await self._notify_disconnect()

    # ── chat_send：两段式 ack + per-request body task（R17-L1）──
    async def chat_send(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        auth_token: str | None = None,
        app_id: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> dict[str, Any]:
        """对位 _bot_websocket_client.py:240-248——纯数据边界 ok 契约。"""
        if not self._connected:
            return {
                "ok": False,
                "error": {"code": "NOT_CONNECTED", "message": "not connected"},
            }
        if attachments:
            # B3 的请求级 v3 重路由在编排层分流；到达这里属编排缺口——
            # 显式错误，绝不静默丢参。
            return {
                "ok": False,
                "error": {
                    "code": "ATTACHMENTS_UNSUPPORTED",
                    "message": "A2A path cannot carry attachments; "
                    "orchestration should route to v3",
                },
            }
        rid = f"{session_key}:{time.time_ns()}"
        wire_message = {
            "messageId": rid,
            "contextId": session_key,
            "role": "ROLE_USER",
            "parts": [{"text": _message_to_text(message)}],
        }
        metadata = _flatten_metadata(chat_metadata, app_id, timeout_ms)
        metadata.setdefault("traceId", rid)
        body = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": "SendStreamingMessage",
            "params": {"message": wire_message, "metadata": metadata},
        }
        headers = {
            **self.headers,
            "X-IAM-Token": auth_token or "OPEN_API:NOT_PROVIDED",
            "Accept": "text/event-stream",
        }
        http_timeout = (timeout_ms / 1000) if timeout_ms else None
        client = self._http(timeout=http_timeout)
        try:
            resp = await client.send(
                client.build_request("POST", self.rpc_url, json=body, headers=headers),
                stream=True,
            )
        except Exception as e:
            await client.aclose()
            return {
                "ok": False,
                "error": {"code": "TRANSPORT_ERROR", "message": str(e)[:200]},
            }
        # E1 错误二分：非 SSE 响应 = JSON-RPC 错误体（先不会有流）
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or "text/event-stream" not in ctype:
            data: dict[str, Any] = {}
            try:
                data = json.loads((await resp.aread()).decode(errors="replace")) or {}
            except Exception:
                pass
            await resp.aclose()
            await client.aclose()
            err = (data.get("error") or {}).get("data") or {}
            return {
                "ok": False,
                "error": {
                    "code": err.get("reason", f"HTTP_{resp.status_code}"),
                    "message": ((data.get("error") or {}).get("message", ""))[:200],
                    "retryable": False,
                },
            }
        ctx = _RunCtx(rid, session_key)
        self._runs[rid] = ctx
        self._session_runs[session_key] = rid
        # P1-5：run 上下文记录该次许可——后续 CancelTask/ResolveInteraction
        # 不能只带 proxypass 头（gate 开启会 auth_required 拒）。
        ctx.auth_token = auth_token or "OPEN_API:NOT_PROVIDED"
        ctx.task = asyncio.create_task(self._consume_stream(ctx, resp, client))
        return {"ok": True, "runId": rid, "a2a": True}

    # ── T1/E1/M1：流消费（终态单次 + 错误面 + 无损路由）──
    async def _consume_stream(
        self, ctx: _RunCtx, resp: httpx.Response, client: httpx.AsyncClient
    ) -> None:
        try:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = json.loads(line[6:])
                if "error" in body:
                    err_data = body["error"].get("data") or {}
                    await self._emit_terminal_error(
                        ctx,
                        err_data.get("reason", "a2a_error"),
                        body["error"].get("message", ""),
                    )
                    return
                await self._handle_wire_result(ctx, body.get("result") or {})
            else:
                # E1：EOF 无终态不得视为成功
                if not ctx.terminal_emitted:
                    await self._emit_terminal_error(
                        ctx,
                        "STREAM_CLOSED_WITHOUT_TERMINAL",
                        "stream ended before terminal status",
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("BotA2aClient SSE consume failed rid=%s", ctx.rid)
            if not ctx.terminal_emitted:
                await self._emit_terminal_error(ctx, "STREAM_ERROR", str(e)[:200])
        finally:
            await resp.aclose()
            await client.aclose()
            self._runs.pop(ctx.rid, None)

    async def _handle_wire_result(self, ctx: _RunCtx, result: dict[str, Any]) -> None:
        for member in ("statusUpdate", "artifactUpdate", "task", "message"):
            if member in result:
                await getattr(self, f"_on_{member}")(ctx, result[member])
                return

    async def _on_task(self, ctx: _RunCtx, task: dict[str, Any]) -> None:
        # 1c：task 快照的 id = 引擎 kernel run id（CancelTask 定位键）。
        if not ctx.engine_task_id and isinstance(task.get("id"), str):
            ctx.engine_task_id = task["id"]
        logger.debug(
            "[a2a] task opened rid=%s state=%s",
            ctx.rid,
            (task.get("status") or {}).get("state"),
        )

    async def _on_artifactUpdate(  # noqa: N802 — wire 成员名反射分发（getattr(self, f"_on_{member}")）
        self, ctx: _RunCtx, au: dict[str, Any]
    ) -> None:
        # 1c：流帧 taskId 即引擎 run id——首见即登记（SSE 订阅时序下
        # task 快照不保证出现，statusUpdate/artifactUpdate 是可靠来源）。
        if not ctx.engine_task_id and isinstance(au.get("taskId"), str):
            ctx.engine_task_id = au["taskId"]
        parts = (au.get("artifact") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if au.get("lastChunk") and not au.get("append"):
            # T1：artifact 终稿只更新内容快照——不发 chat final、不置完成
            ctx.final_text = text
            return
        await self._dispatch(
            "chat",
            {
                "state": "delta",
                "deltaText": text,
                "runId": au.get("taskId"),
                "sessionKey": ctx.session_key,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
                "seq": (au.get("metadata") or {}).get("seq"),
            },
        )

    async def _on_statusUpdate(  # noqa: N802 — wire 成员名反射分发（getattr(self, f"_on_{member}")）
        self, ctx: _RunCtx, su: dict[str, Any]
    ) -> None:
        meta = su.get("metadata") or {}
        state = (su.get("status") or {}).get("state", "")
        if not ctx.engine_task_id and isinstance(su.get("taskId"), str):
            ctx.engine_task_id = su["taskId"]
        base = {
            "runId": su.get("taskId"),
            "sessionKey": ctx.session_key,
            "seq": meta.get("seq"),
        }
        frame = meta.get("engine_frame")

        if state == "TASK_STATE_INPUT_REQUIRED":
            payload = dict(frame) if isinstance(frame, dict) else {}
            payload.setdefault("interactionId", meta.get("interactionId"))
            payload.setdefault("state", "interaction.requested")
            payload["sessionKey"] = ctx.session_key
            await self._dispatch("interaction.requested", payload)
            return
        if (
            meta.get("interactionId")
            and state == "TASK_STATE_WORKING"
            and meta.get("kind") not in ("agent", "resolve_conflict")
        ):
            # R18-1b：approval_resolved（interactionId 从 approvalId 提取）
            # 与 interaction.resolved 同一事件身份出站。
            payload = dict(frame) if isinstance(frame, dict) else {}
            payload.setdefault("interactionId", meta.get("interactionId"))
            payload["state"] = "interaction.resolved"
            payload["sessionKey"] = ctx.session_key
            await self._dispatch("interaction.resolved", payload)
            return
        if meta.get("kind") == "agent":
            payload = dict(frame) if isinstance(frame, dict) else {}
            payload.setdefault("stream", payload.get("stream", ""))
            payload.setdefault("state", payload.get("state", ""))
            payload["sessionKey"] = ctx.session_key
            # R17-2：original_event 决定消费者回调路由
            v3_event = meta.get("original_event", "agent")
            await self._dispatch(v3_event, payload)
            return

        if meta.get("final") in ("true", True):
            if ctx.terminal_emitted:
                return
            ctx.terminal_emitted = True
            if state == "TASK_STATE_COMPLETED":
                text = ctx.final_text or ""
                await self._dispatch(
                    "chat",
                    {
                        **base,
                        "state": "final",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": text}],
                        },
                    },
                )
            else:
                # FAILED/CANCELED/REJECTED → error 终态（_on_chat.error 分支）
                err_meta = (frame or {}) if isinstance(frame, dict) else {}
                await self._dispatch(
                    "chat",
                    {
                        **base,
                        "state": "error",
                        "errorMessage": meta.get("errorMessage")
                        or err_meta.get("errorMessage")
                        or f"engine terminal {state}",
                        "errorCode": state,
                    },
                )
            return
        await self._dispatch("chat", {**base, "state": state})

    async def _on_message(self, ctx: _RunCtx, msg: dict[str, Any]) -> None:
        parts = msg.get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        await self._dispatch(
            "chat",
            {
                "state": "message",
                "sessionKey": ctx.session_key,
                "runId": ctx.rid,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            },
        )

    async def _emit_terminal_error(self, ctx: _RunCtx, code: str, message: str) -> None:
        # P1-2：必须 await——否则 coroutine 从未启动，SSE error/EOF 无终态/
        # 读异常三条分支都会静默丢终态（上层悬挂）。
        ctx.terminal_emitted = True
        await self._dispatch(
            "chat",
            {
                "state": "error",
                "sessionKey": ctx.session_key,
                "runId": ctx.rid,
                "errorMessage": message,
                "errorCode": code,
            },
        )

    async def _dispatch(self, event_name: str, payload: dict[str, Any]) -> None:
        """R18-E2E 签名修齐：与 BotWebSocketClient 的真实分发契约一致——
        精确 handler 单参 ``handler(payload)`` 被调后协程视情况 await（recv
        循环 :448-453）；通配 "*" 以 ``(event_name, payload)`` 调（:458-463）。
        （原实现把两参传给精确 handler——E2E 连接编排时当场暴露。）"""
        handler = self._event_handlers.get(event_name)
        if handler:
            try:
                coro = handler(payload)
                if asyncio.iscoroutine(coro):
                    await coro
            except Exception:
                logger.exception("[a2a] handler %s failed", event_name)
        wildcard = self._event_handlers.get("*")
        if wildcard:
            try:
                coro = wildcard(event_name, payload)
                if asyncio.iscoroutine(coro):
                    await coro
            except Exception:
                logger.exception("[a2a] wildcard handler failed")

    # ── abort（1c：chat.abort → CancelTask 转发）──
    def _supports_cancel(self) -> bool:
        """引擎能力卡是否声明 cancel extension（灰度期老引擎没有——
        abort 诚实不转发，绝不向不支持的引擎猜语义）。"""
        card = ((self._server_info or {}).get("server") or {}).get("card")
        if not isinstance(card, dict):
            return False
        extensions = (card.get("capabilities") or {}).get("extensions")
        if not isinstance(extensions, list):
            return False
        return any(
            isinstance(e, dict) and e.get("url") == _CANCEL_EXT_URI for e in extensions
        )

    async def chat_abort(
        self, session_key: str, run_id: str | None = None
    ) -> dict[str, Any]:
        """chat.abort → CancelTask（设计 §7）。

        语义分层（每层显式，无假成功）：
          - 引擎未声明 cancel 能力（老引擎）：保留原行为——本地 body-task
            取消是**订阅断流** + 诚实 CANCEL_UNSUPPORTED；
          - 引擎侧 task id 未知（未开流/已收尾/未知 session）：RUN_NOT_TRACKED
            ，不盲发 CancelTask，本地断流 fallback 保留；
          - engine 显式拒绝（task_terminal 等）：ok=False 原样透传引擎
            reason——本地流不动（引擎语义成立，run 会自然收尾，误杀本地
            task 只会让消费方悬挂）；
          - 传输/通道错误：TRANSPORT_ERROR/HTTP_*（ok=False）；
          - 成功（result.task.status.state==TASK_STATE_CANCELED，E4 式
            真实契约判定）：ok=True——本地 body task **不**取消：
            engine 沿 SSE 推 CANCELED 终态，消费方经 final 分支收
            terminal error（订阅断流语义由引擎侧终态承载）。
        """
        rid = run_id or self._session_runs.get(session_key)
        ctx = self._runs.get(rid or "")

        if not self._supports_cancel():
            if ctx and ctx.task and not ctx.task.done():
                # 订阅断流：让消费方尽快脱困——但 abort 面本身不成功。
                ctx.task.cancel()
            return {
                "ok": False,
                "a2a": True,
                "error": {
                    "code": "CANCEL_UNSUPPORTED",
                    "message": "engine does not declare cancel capability",
                    "retryable": False,
                },
            }

        task_id = ctx.engine_task_id if ctx else None
        if not task_id:
            if ctx and ctx.task and not ctx.task.done():
                ctx.task.cancel()
            return {
                "ok": False,
                "a2a": True,
                "error": {
                    "code": "RUN_NOT_TRACKED",
                    "message": "no engine task id known for session/run",
                    "retryable": False,
                },
            }

        request_id = f"cxl-{uuid.uuid4().hex[:12]}"
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "CancelTask",
            "params": {"taskId": task_id},
        }
        # P1-5：CancelTask 带 run 记录的调用方许可（gate 开启时必需）。
        cancel_headers = dict(self.headers)
        if ctx.auth_token:
            cancel_headers["X-IAM-Token"] = ctx.auth_token
        try:
            async with self._http(timeout=30.0) as http:
                resp = await http.post(
                    self.rpc_url, json=payload, headers=cancel_headers
                )
        except Exception as e:
            return {
                "ok": False,
                "a2a": True,
                "error": {
                    "code": "TRANSPORT_ERROR",
                    "message": str(e)[:200],
                    "retryable": False,
                },
            }
        if resp.status_code != 200:
            return {
                "ok": False,
                "a2a": True,
                "error": {
                    "code": f"HTTP_{resp.status_code}",
                    "message": resp.text[:200],
                    "retryable": False,
                },
            }
        try:
            body = resp.json()
        except Exception:
            body = {}
        if not isinstance(body, dict) or body.get("id") != request_id:
            return {
                "ok": False,
                "a2a": True,
                "error": {
                    "code": "CANCEL_MALFORMED",
                    "message": f"unparseable http_{resp.status_code}",
                    "retryable": False,
                },
            }
        has_result = isinstance(body.get("result"), dict)
        has_error = isinstance(body.get("error"), dict)
        if has_error and not has_result:
            err = body["error"]
            return {
                "ok": False,
                "a2a": True,
                "error": {
                    "code": (err.get("data") or {}).get("reason", "a2a_error"),
                    "message": err.get("message", "")[:200],
                    "retryable": False,
                },
            }
        # E4 纪律：成功只认真实契约——result.task.status.state==CANCELED；
        # result 但 state 不符（或 result/error 双有/双无）都不得判成功。
        task = (body.get("result") or {}).get("task")
        state = ((task or {}).get("status") or {}).get("state")
        if has_result and not has_error and state == "TASK_STATE_CANCELED":
            return {
                "ok": True,
                "a2a": True,
                "task": {"id": (task or {}).get("id"), "state": state},
            }
        return {
            "ok": False,
            "a2a": True,
            "error": {
                "code": "CANCEL_RESULT_INVALID",
                "message": f"unexpected cancel result: {str(body)[:200]}",
                "retryable": False,
            },
        }

    # ── chat_inject（R16-1 签名；R17-7 metadata 平铺+timeout）──
    async def chat_inject(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        auth_token: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> dict[str, Any]:
        """对位 _bot_websocket_client.py:281-314——纯数据边界。"""
        if attachments:
            return {
                "ok": False,
                "raise_": False,
                "error": {
                    "code": "ATTACHMENTS_UNSUPPORTED",
                    "message": "A2A path cannot carry attachments; "
                    "orchestration should route to v3",
                },
            }
        mid = f"inj-{uuid.uuid4().hex[:12]}"
        metadata = _flatten_metadata(chat_metadata, None, timeout_ms)
        metadata.setdefault("traceId", mid)
        payload = {
            "jsonrpc": "2.0",
            "id": mid,
            "method": "InjectContext",
            "params": {
                "contextId": session_key,
                "message": {
                    "role": "ROLE_USER",
                    "messageId": mid,
                    "parts": [{"text": _message_to_text(message)}],
                },
                "metadata": metadata,
            },
        }
        headers = {**self.headers, "X-IAM-Token": auth_token or "OPEN_API:NOT_PROVIDED"}
        timeout = (timeout_ms / 1000) if timeout_ms else 30.0
        async with self._http(timeout=timeout) as http:
            resp = await http.post(self.rpc_url, json=payload, headers=headers)
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "error": {
                        "code": f"HTTP_{resp.status_code}",
                        "message": resp.text[:200],
                    },
                }
            body = resp.json()
        if "error" in body:
            return {
                "ok": False,
                "error": {
                    "code": (body["error"].get("data") or {}).get(
                        "reason", "a2a_error"
                    ),
                    "message": body["error"].get("message", "")[:200],
                },
            }
        result = body.get("result") or {}
        status = result.get("inject", "unknown")
        return {
            "ok": status in ("accepted", "duplicate_accepted", "duplicate_in_progress"),
            "status": status,
            "a2a": True,
        }

    # ── interaction_resolve：真实 InteractionResolution → exchange ──
    async def interaction_resolve(
        self,
        *,
        interaction_id: str,
        resolution: InteractionResolution,
        session_key: str | None = None,
    ) -> EngineInteractionResolveExchange:
        """R17-6：返回真实 dataclass；按 from_frames 等价校验响应
        （id 一致性 + result/error 结构）——不合规响应 accepted=False。

        P1-5：session_key 供请求头取“产生该 interaction 的 run”记录的
        IAM 许可（gate 开启时 ResolveInteraction 必须带）——查不到 run
        上下文时不伪造许可（转交既有 self.headers，引擎按需诚实拒绝）。"""
        resolution_payload: dict[str, Any] = {
            "decision": resolution.decision,
            "kind": resolution.kind,
            "answer": resolution.answer,
            "message": resolution.message,
            "values": resolution.values,
            "answers": resolution.answers,
            "selected_options": [list(o) for o in resolution.selected_options]
            if resolution.selected_options
            else None,
        }
        request = {
            "jsonrpc": "2.0",
            "id": f"res-{interaction_id}",
            "method": "ResolveInteraction",
            "params": {
                "interactionId": interaction_id,
                "resolution": {
                    k: v for k, v in resolution_payload.items() if v is not None
                },
            },
        }
        async with self._http(timeout=30.0) as http:
            resolve_headers = dict(self.headers)
            ctx = None
            if session_key is not None:
                ctx = self._runs.get(self._session_runs.get(session_key, ""))
            if ctx is not None and ctx.auth_token:
                resolve_headers["X-IAM-Token"] = ctx.auth_token
            resp = await http.post(self.rpc_url, json=request, headers=resolve_headers)
        try:
            response_body = resp.json()
        except Exception:
            response_body = {"__malformed__": f"unparseable http_{resp.status_code}"}
        # R18-3: 类型/关联/互斥三段验证（from_frames 等价强度）——
        # 任何一段不过都是 accepted=False，成功不从 error 真值推导。
        fail_reason: str | None = "malformed or mismatched response"
        # 0) 类型必须是 dict（括号内的([]/标量在此直接拒）——这一步在
        # resp.json() 可能返回 list 的情况下是唯一安全面。
        if not isinstance(response_body, dict):
            response_body = {"__malformed__": "non-object body"}
        # 1) 关联 + 互斥（正文为 dict 才进入）
        if not response_body.get("__malformed__"):
            # 2) 关联：id 必须回显请求 id
            if response_body.get("id") != request["id"]:
                pass
            else:
                has_result = "result" in response_body and isinstance(
                    response_body["result"], dict
                )
                # R18-3: error 键在场且其值可判读（dict 或 null 之外的真值）。
                # '{}' 是可判读错误对象（键存在本身已构成互斥冲突）——
                # bool(get(...)) 不作为互斥判据judge（评审三例里 {} 被当无错）。
                has_error = "error" in response_body and isinstance(
                    response_body["error"], dict
                )
                # 3) 互斥：result/error 各自结构合法且不同时存在
                if has_result and not has_error:
                    fail_reason = None
                elif has_error and not has_result:
                    err = response_body["error"]
                    fail_reason = (
                        err.get("message")
                        or (err.get("data") or {}).get("reason", "")
                        or "engine resolve error"
                    )
                # 双有/双无 → 维持 fail_reason
        if response_body.get("__malformed__"):
            fail_reason = str(response_body["__malformed__"])
        if fail_reason is not None:
            return EngineInteractionResolveExchange(
                request=request,
                response=response_body,
                accepted=False,
                error_message=fail_reason,
            )
        return EngineInteractionResolveExchange(
            request=request,
            response=response_body,
            accepted=True,
            error_message=None,
        )
