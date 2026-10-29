"""A2A v1.0 JSON-RPC binding — FastAPI router.

Thin shell: request parse → validate → auth-gate → kernel call → wire →
Response. No state (kernel module owns it all).

Envelope discipline (JSON-RPC 2.0):
  - every response/error carries the request's own `id` (never messageId),
  - handlers return plain result dicts; `rpc()` wraps them,
  - SSE generators close over the request id for stream event envelopes,
  - boundary validation: malformed JSON → -32700, non-object body → -32600,
    missing message/messageId → -32602 (never a naked 500).

Security parity with the WS surface (R12-4): message-sending methods run the
same AuthGate check ws_server applies at :1172-1201 — get_switch → x-iam-token
→ verify_chat_send — before any kernel registration or engine dispatch. An
A2A caller can never succeed where the WS caller would be rejected.

Extension negotiation (R12-5, design §8.5.2): a request declaring the retry
extension via the A2A-Extensions header must carry a boolean
metadata.avernet.retryOnly — missing or mistyped is rejected (-32602), it is
never silently treated as FIRST.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi_injector import Injected
from starlette.responses import JSONResponse, Response, StreamingResponse

from engine.community.api.transport.a2a import wire
from engine.community.api.transport.a2a.card import EXTENSION_URIS, make_agent_card
from engine.community.api.transport.a2a.engine_bridge import execute_and_stream
from engine.community.api.transport.a2a.kernel import A2aKernel, KeyConflictError
from engine.community.core.chat.models import ChatAbortRequest
from engine.community.plugin_api.auth_gate.protocol import AuthGateService

log = logging.getLogger("engine-a2a")

router = APIRouter()

# Module-level kernel singleton (one per FastAPI process).
_kernel = A2aKernel()

# Strong references to in-flight engine_bridge tasks: asyncio only weakly
# tracks tasks, so an unreferenced fire-and-forget task can be garbage
# collected mid-run and silently kill the event stream.
_BRIDGE_TASKS: set[asyncio.Task] = set()

# Methods that can trigger engine execution or state mutation — gated
# exactly like ws_server's handling of chat.send / chat.inject. CancelTask
# 是写操作（改写 run 终态），与 ResolveInteraction 同级进 gate；
# GetTask/SubscribeToTask/ReplayEvents 只读，不进（既有先例）。
_GATED_METHODS = frozenset({"SendMessage", "SendStreamingMessage",
                            "InjectContext", "ResolveInteraction",
                            "CancelTask"})

_HANDLERS: dict[str, Any] = {}

# HTTP header the WS surface reads the same principal from (frame param
# x-iam-token); A2A HTTP callers pass it as a request header.
_IAM_TOKEN_HEADER = "x-iam-token"


def _sse(data: Any) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# SSE 空闲心跳：流订阅者在引擎忙窗（模型思考/工具执行等长静默段）
# 没有任何字节输出，中间 L7（proxypass/SLB 等）会按 idle-timeout 掐断
# 静默连接——BaaS 侧表现为 httpcore.ReadError、run 判失败（fail-hard
# 无降级，2026-10-28 预发灰度 11 例断流实证）。注释帧是 SSE 保活标
# 准做法：客户端一律忽略，wire 语义零污染；v3 WS 的等价物是协议
# 层 ping/pong。15s 给常见 60s 代理 idle 线留 4 倍裕量。
_SSE_KEEPALIVE_SECONDS = 15.0
_SSE_KEEPALIVE_FRAME = ": keepalive\n\n"

# _get_with_keepalive 的超时哨兵——与队列 None 终结信标严格区分。
_KEEPALIVE_TICK = object()


async def _get_with_keepalive(q: Any) -> Any:
    """事件读取 + 空闲心跳：静默超时返回心跳哨兵，调用方回写注释帧。

    q.get() 被取消不丢事件——put 只唤起 getter，值仍留在队列中下次
    自取（asyncio.Queue 契约），wait_for 超时切走零损失。
    """
    try:
        return await asyncio.wait_for(q.get(), _SSE_KEEPALIVE_SECONDS)
    except asyncio.TimeoutError:
        return _KEEPALIVE_TICK


def _rpc_response(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error_response(rid: Any, error: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": error})


def _invalid_params(rid: Any, message: str) -> JSONResponse:
    return _rpc_error_response(rid, {"code": -32602, "message": message})


class _ParamError(ValueError):
    """Boundary type/shape violation → JSON-RPC -32602 (never a naked 500)."""


@router.get("/.well-known/agent-card.json")
async def agent_card() -> dict[str, Any]:
    from engine.community.config import get_local_base_url
    return make_agent_card(get_local_base_url(),
                           resolve_supported=_engine_resolve_supported())


def _canonical_context(method: str, params: dict[str, Any], rid: Any
                        ) -> tuple[str | None, str]:
    """R13-3: the single context both the auth gate and execution use.

    Extracted per-method BEFORE the gate runs — the gate can never check one
    session while execution writes another. For InjectContext the executed
    context is params.contextId; when the message object carries a different
    contextId the request is rejected outright (zero dispatch, zero
    broadcast). Send methods execute against message.contextId.
    Returns (canonical_ctx_or_None, message_text) — ctx None means the
    handler's own validation rejects later (kept out of the gate path).
    """
    if method == "ResolveInteraction":
        # E4/16-7: canonical session 从 interaction 所属 run 定位（gate
        # 与执行同源，R13-3 约束）；text=gate 判定用的 decision。
        iid = params.get("interactionId")
        if not isinstance(iid, str) or not iid:
            raise _ParamError("interactionId is required (string)")
        located = _kernel.interaction_run(iid)
        if located is None or located[1].state in wire.TERMINAL_STATES:
            # gate 前即不存在——留给 handler 出结构化 not_found
            return None, ""
        run = located[1]
        resolution = params.get("resolution")
        decision = (resolution.get("decision", "") if isinstance(
            resolution, dict) else "")
        return run.context_id, str(decision)

    if method == "CancelTask":
        # 1c/TC-CANCEL：canonical session = 目标 run 的 context（gate 与
        # 状态写同源）；text 为空（无消息载荷）。
        tid = params.get("taskId")
        if not isinstance(tid, str) or not tid:
            raise _ParamError("taskId is required (string)")
        r = _kernel.get_run(tid)
        if r is None:
            # gate 前即不存在——留给 handler 出结构化 not_found
            return None, ""
        return r.context_id, ""

    msg = params.get("message")
    if method == "InjectContext":
        ctx = params.get("contextId")
        if isinstance(msg, dict):
            msg_ctx = msg.get("contextId")
            if msg_ctx is not None and msg_ctx != ctx:
                raise _ParamError(
                    "contextId conflict: message.contextId must match "
                    "params.contextId")
        text = _message_text(msg) if isinstance(msg, dict) else ""
        if not isinstance(ctx, str):
            raise _ParamError("contextId is required (string)")
        return ctx, text
    if isinstance(msg, dict):
        ctx = msg.get("contextId")
        if ctx is not None and not isinstance(ctx, str):
            raise _ParamError("message.contextId must be a string")
        return (ctx if isinstance(ctx, str) else None), _message_text(msg)
    return None, ""


async def _auth_gate_guard(request: Request, rid: Any, method: str,
                            session_key: str, message_text: str,
                            auth_gate_service: AuthGateService) -> str | None:
    """ws_server:1172-1201 parity.

    Returns the IAM-derived idempotency key override on success (caller
    substitutes it for the client messageId, exactly as ws_server rewrites
    params.idempotencyKey), or None when the caller may proceed with its own
    key. On rejection a JSONResponse is returned instead (truthy).
    """
    auth_gate_enabled = await auth_gate_service.get_switch()
    if not auth_gate_enabled:
        return None

    iam_token = request.headers.get(_IAM_TOKEN_HEADER, "")
    if not iam_token:
        return _rpc_error_response(  # type: ignore[return-value]
            rid, {"code": -32000, "message": "Missing x-iam-token",
                  "data": {"reason": "auth_required"}})

    from engine.community.api.transport.auth_gate import verify_chat_send
    try:
        gate_result = await verify_chat_send(
            auth_gate_service=auth_gate_service,
            session_key=session_key,
            message=message_text,
            iam_token=iam_token,
        )
    except Exception as e:
        # ws_server logs and allows on gate exceptions; keep the parity.
        import logging
        logging.getLogger("engine-a2a").warning(
            "auth_gate exception, allowing %s: %s", method, e)
        return None

    if gate_result and not gate_result.allowed:
        return _rpc_error_response(  # type: ignore[return-value]
            rid, {"code": -32000,
                  "message": gate_result.error_message or "zero_check failed",
                  "data": {"reason": "ZERO_CHECK_FAILED"}})

    return gate_result.idempotency_key or None


@router.post("/rpc")
async def rpc(request: Request,
              auth_gate_service: AuthGateService = Injected(AuthGateService)
              ) -> Response:
    # ── boundary validation (R12-8): malformed input never becomes a 500 ──
    try:
        body = await request.json()
    except Exception:
        return _rpc_error_response(None, {"code": -32700,
                                           "message": "Parse error"})
    if not isinstance(body, dict):
        return _rpc_error_response(None, {"code": -32600,
                                           "message": "Invalid Request"})
    rid = body.get("id")
    method = body.get("method", "")
    if not isinstance(method, str):
        return _rpc_error_response(rid, {"code": -32600,
                                          "message": "method must be a string"})
    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _invalid_params(rid, "params must be an object")

    handler = _HANDLERS.get(method)
    if handler is None:
        return _rpc_error_response(rid, {"code": -32601,
                                          "message": f"Method not found: {method}"})

    # ── R12-5: extension negotiation before the handlers see params ──
    declared = _declared_extensions(request)
    if EXTENSION_URIS["retry"] in declared:
        retry_val = (params.get("metadata") or {}).get("avernet.retryOnly") \
            if isinstance(params.get("metadata"), dict) else None
        if retry_val is None:
            return _invalid_params(
                rid, "avernet.retryOnly required when retry extension declared")

    try:
        # ── R13-3: canonical context BEFORE the gate — the gate and
        # execution share one value by construction; a context mismatch
        # between message and params is rejected here, pre-gate.
        # ── R12-4: same AuthGate the WS surface enforces, before any
        # registration or dispatch. Gate replacement of the idempotency key
        # mirrors ws_server's params.idempotencyKey rewrite.
        gated_key = None
        if method in _GATED_METHODS:
            gate_ctx, gate_text = _canonical_context(method, params, rid)
            guard = await _auth_gate_guard(request, rid, method, gate_ctx,
                                           gate_text, auth_gate_service)
            if isinstance(guard, JSONResponse):
                return guard
            gated_key = guard

        outcome = await handler(params, rid, gated_key)
    except KeyConflictError:
        return _rpc_error_response(rid, {"code": -32003,
                                          "data": {"reason": "key_conflict"}})
    except _ParamError as e:
        return _invalid_params(rid, str(e))
    except ValueError as e:
        msg = str(e)
        if "contextId" in msg:
            return _rpc_error_response(rid, wire.wire_context_id_required())
        return _invalid_params(rid, msg)
    except LookupError:
        return _rpc_error_response(rid, wire.wire_replay_refused("not_found"))
    except RuntimeError as e:
        # R12-M3: capacity hard-refusal — the v3 openclaw_resume parity.
        return _rpc_error_response(rid, {"code": -32000,
                                          "message": str(e)[:200],
                                          "data": {"reason": "capacity"}})

    if isinstance(outcome, Response):
        return outcome
    if isinstance(outcome, dict) and "error" in outcome:
        return _rpc_error_response(rid, outcome["error"])
    return _rpc_response(rid, outcome)


def _declared_extensions(request: Request) -> list[str]:
    header = request.headers.get("A2A-Extensions", "")
    return [part.strip() for part in header.split(",") if part.strip()]


# ── Retry flag (D14 §8.5.2) ──

def _retry_flag(params: dict[str, Any]) -> str:
    meta = params.get("metadata")
    if not isinstance(meta, dict) or "avernet.retryOnly" not in meta:
        return "first"
    val = meta["avernet.retryOnly"]
    if not isinstance(val, bool):
        raise _ParamError("avernet.retryOnly must be boolean")
    return "retry_only" if val else "first"


def _message_text(msg: dict[str, Any]) -> str:
    parts = msg.get("parts")
    if not isinstance(parts, list):
        return ""
    out = []
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            out.append(p["text"])
    return "".join(out)


def _alive_time_ms(params: dict[str, Any]) -> int | None:
    """R4-3：把客户端超时贯穿到引擎（ChatRequest.aliveTime 的对位）。

    BaaS 侧 R17-7 把 timeout_ms 平铺进 metadata.timeoutMs（int 或数字
    串）；缺失 → None（引擎默认语义不变）。v3 的对照行为是
    ws_server 把 wire timeoutMs 传给 ChatRequest.aliveTime——灰度切换
    不得丢这一参（丢即引擎按自身默认 idle 超时判
    STREAM_TERMINATED_WITHOUT_FINAL）。非法值 → -32602 边界错误，不
    带病下行。
    """
    meta = params.get("metadata")
    if not isinstance(meta, dict):
        return None
    raw = meta.get("timeoutMs")
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise _ParamError("metadata.timeoutMs must be a positive integer "
                          "(milliseconds)")
    if isinstance(raw, str):
        if not raw.isdigit():
            raise _ParamError("metadata.timeoutMs must be a positive "
                              "integer (milliseconds)")
        raw = int(raw)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise _ParamError("metadata.timeoutMs must be a positive integer "
                          "(milliseconds)")
    return raw


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _get_message(params: dict[str, Any]) -> dict[str, Any]:
    """Extract + validate the `message` member (messageId required, str)."""
    msg = params.get("message")
    if not isinstance(msg, dict):
        raise _ParamError("params.message is required and must be an object")
    mid = msg.get("messageId")
    if not isinstance(mid, str) or not mid:
        raise _ParamError("message.messageId is required (string)")
    return msg


# ── Shared execution-once dispatch (R12-1: both entries start the engine) ──

def _ensure_dispatched(rid: str, ctx: str, text: str, created: bool,
                       alive_ms: int | None = None) -> None:
    """Exactly-once engine dispatch across both send entries: D14 "FIRST |
    absent | register and begin execution". A dedup hit (created=False)
    attaches to the existing stream instead of dispatching again.

    alive_ms：R4-3 客户端超时（metadata.timeoutMs）透传引擎
    ChatRequest.aliveTime——v3 对位参,灰度不得丢。"""
    if not created:
        return
    task = asyncio.create_task(
        execute_and_stream(_kernel, rid, ctx, text, alive_ms=alive_ms))
    _BRIDGE_TASKS.add(task)
    task.add_done_callback(_BRIDGE_TASKS.discard)


# ── SendMessage ──

async def _send_message(params: dict[str, Any], rid: Any,
                        gated_key: str | None) -> dict[str, Any]:
    msg = _get_message(params)
    ctx = msg.get("contextId")
    if not isinstance(ctx, str) or not ctx:
        raise ValueError("contextId is required")
    # 边界校验先行（R4-3/与 retryOnly 同口径）：非法 timeoutMs 在开 run
    # 前拒绝——不留"注册成功但参数病"的孤儿 run。
    alive_ms = _alive_time_ms(params)
    out = await _kernel.send_message(ctx, _message_text(msg),
                                     gated_key or msg["messageId"],
                                     _retry_flag(params))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return {"error": wire.wire_unknown_outcome()}
    run_rid = out["task"]["id"]
    created = out["task"]["metadata"]["created"]
    # R12-1: non-streaming FIRST must start execution too — the run cannot
    # be left dangling at SUBMITTED. Dedup hits dispatch nothing (C1).
    _ensure_dispatched(run_rid, ctx, _message_text(msg), created, alive_ms)
    return out

_HANDLERS["SendMessage"] = _send_message


# ── SendStreamingMessage ──

async def _send_streaming(params: dict[str, Any], req_id: Any,
                          gated_key: str | None) -> Response:
    msg = _get_message(params)
    ctx = msg.get("contextId")
    if not isinstance(ctx, str) or not ctx:
        raise ValueError("contextId is required")
    # 边界校验先行（R4-3/与 retryOnly 同口径）：非法 timeoutMs 在开 run
    # 前拒绝——不留"注册成功但参数病"的孤儿 run。
    alive_ms = _alive_time_ms(params)
    out = await _kernel.open_run(ctx, gated_key or msg["messageId"],
                                 _retry_flag(params),
                                 _digest(_message_text(msg)))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return _rpc_error_response(req_id, wire.wire_unknown_outcome())
    rid, created = out

    _ensure_dispatched(rid, ctx, _message_text(msg), created, alive_ms)

    backlog, q = await _kernel.subscribe(rid, since_seq=0)
    return _stream_response(req_id, rid, backlog, q)

_HANDLERS["SendStreamingMessage"] = _send_streaming


# ── GetTask (design §8.1 third-route fallback after replay refusal) ──

async def _get_task(params: dict[str, Any], req_id: Any,
                    _gated_key: str | None) -> dict[str, Any]:
    tid = params.get("taskId")
    if not isinstance(tid, str) or not tid:
        raise _ParamError("taskId is required (string)")
    r = _kernel.get_run(tid)
    if r is None:
        return {"error": {"code": -32000,
                          "message": "task not found or expired",
                          "data": {"reason": "not_found_or_expired"}}}
    # R12-7: content snapshot survives cache_limit — the fallback answers
    # with the generated text, not just the status.
    artifacts: list[dict[str, Any]] = []
    if r.latest_text:
        artifacts.append({"artifactId": f"text-{tid}",
                          "parts": [{"text": r.latest_text}],
                          "metadata": {}})
    return {"task": {"id": r.run_id, "contextId": r.context_id,
                      "status": {"state": r.state}, "artifacts": artifacts,
                      "metadata": {}}}

_HANDLERS["GetTask"] = _get_task


# ── CancelTask (1c/TC-CANCEL，设计 §7：幂等；终态后取消 → 标准错误) ──

# 1c+：引擎 abort 上行（best-effort）的响应时序上限。与 _RESOLVE/_INJECT 家族同口径；
# 端口自带更短的 RPC 超时（claude_code port chat.abort 10s），这里的
# wait_for 只是病态 adapter 的兜底护栏（BaaS CancelTask HTTP 窗口 30s）。
_ABORT_FORWARD_TIMEOUT_SECONDS = 30.0

# abort 上行结局——CancelTask 响应以 engineAbort 字段如实附录，供调用方
# 判读/告警。BaaS 成功判定不变（仍钉 result.task.status.state==CANCELED）。
# 语义边界（R3-3）：传输任务终结与引擎停止是两个不同的事实——
# engineAbort 除 confirmed 外的任何值都不保证引擎已停止。
_ABORT_CONFIRMED = "confirmed"       # ok=True, aborted=True：引擎确认取消目标运行
_ABORT_NOT_FOUND = "not_found"       # ok=True, aborted=False：引擎侧无匹配的在跑运行
_ABORT_REFUSED = "refused"            # ok=False：引擎未执行中止（不应一概推断为业务拒绝）
_ABORT_TIMEOUT = "timeout"           # 等待 abort 结果超时——是否已取消不可知
_ABORT_UNAVAILABLE = "unavailable"   # abort 调用无法完成——是否已停止不可知


async def _forward_engine_abort(run: Any) -> str:
    """1c+：cancel_run 成功后向引擎发真实 ChatService.abort——与
    ws_server._handle_chat_abort 同一构造口径（ChatAbortRequest
    (session_key, run_id)）：session=run 的规范 context（与 gate 判定
    同源，R13-3），run_id=E4 桥注入的引擎原生 id（未登记时 None——
    两侧 port 均支持 session 级中止：claude_code 省略 runId，
    openclaw 传 ""）。auth=None 与 inject/resolve 上行一致（HTTP 面的
    文档化默认）。

    两层事实语义（R3-3）：
    - 传输取消：cancel_run 已终结订阅者与 run 终态，T1 挡住引擎迟到帧；
      响应 state==CANCELED 对传输面属实。
    - 引擎停止：本方法是 best-effort 上行，结局经返回值如实附录；
      非 confirmed 时引擎可能仍在执行，调用方不得据传输取消成功
      推断执行已停止。

    不重试、不回滚、不上浮改判：重复 CancelTask 被 task_terminal 挡下后
    不会再发 abort；取消确认与 abort 重试机制留作后续设计——本 PR 不
    引入"取消中"状态机。连接不可达（unavailable）也不得推断"引擎已停
    止"。
    """
    from engine.community.api.transport.a2a.engine_bridge import _chat_plugin

    try:
        chat = _chat_plugin()
        result = await asyncio.wait_for(
            chat.abort(
                ChatAbortRequest(session_key=run.context_id,
                                 run_id=run.native_run_id),
                auth=None,
            ),
            timeout=_ABORT_FORWARD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning("engine abort timed out (transport cancel in effect): "
                    "run=%s context=%s", run.run_id, run.context_id)
        return _ABORT_TIMEOUT
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("engine abort unavailable: run=%s %s: %s",
                    run.run_id, type(e).__name__, e)
        return _ABORT_UNAVAILABLE
    if not result.ok:
        log.warning("engine abort refused: run=%s code=%s message=%s "
                    "(transport cancel in effect)", run.run_id,
                    getattr(result.error, "code", "UNKNOWN"),
                    getattr(result.error, "message", ""))
        return _ABORT_REFUSED
    if not result.aborted:
        log.warning("engine abort found no active run: run=%s native=%s "
                    "(transport cancel in effect; engine run already "
                    "finished or unknown to the engine)", run.run_id,
                    run.native_run_id)
        return _ABORT_NOT_FOUND
    return _ABORT_CONFIRMED


async def _cancel_task(params: dict[str, Any], req_id: Any,
                       _gated_key: str | None) -> dict[str, Any]:
    """§8.6：cancel 改 run 状态与订阅者终结——kernel 不触碰
    interaction/resolution 注册表（HITL 记录独立于 run 生命周期）；
    引擎侧 abort 上行（best-effort，结局附录进 engineAbort）由
    _forward_engine_abort 在 cancel_run 之后发起。

    幂等契约（§7 表）：已终态 run 上 cancel 不迁移、不重复终结、不再
    发 abort——标准错误，data 带 {state, already_terminal} 让迟到的
    重复 cancel 可判读 run 终局（CANCELED 与 COMPLETED 同一错误面、
    以 data.state 区分，无静默 no-op 歧义）。
    """
    tid = params.get("taskId")
    if not isinstance(tid, str) or not tid:
        raise _ParamError("taskId is required (string)")
    r = _kernel.get_run(tid)
    if r is None:
        return {"error": {"code": -32000,
                          "message": "task not found or expired",
                          "data": {"reason": "not_found_or_expired"}}}
    if r.state in wire.TERMINAL_STATES:
        return {"error": {"code": -32000,
                          "message": f"task already terminal ({r.state})",
                          "data": {"reason": "task_terminal", "state": r.state,
                                   "already_terminal": True}}}
    snapshot = await _kernel.cancel_run(tid)
    # 1c+：传输面 CANCELED 落定后上行引擎 abort——响应仍按传输面快照
    # （BaaS 成功判定钉在 result.task.status.state==CANCELED，abort 结局
    # 不改成功判定），abort 结局以 engineAbort 如实附录供调用方判读；
    # 引擎 aborted 帧随流回流经 bridge 收口，T1 下与本次 CANCELED 幂等
    # 收敛，迟到不二次终结。
    outcome = await _forward_engine_abort(r)
    return {"task": snapshot, "engineAbort": outcome}


_HANDLERS["CancelTask"] = _cancel_task


# ── SubscribeToTask ──

async def _subscribe_task(params: dict[str, Any], req_id: Any,
                          _gated_key: str | None) -> Response:
    tid = params.get("taskId")
    if not isinstance(tid, str) or not tid:
        raise _ParamError("taskId is required (string)")
    r = _kernel.get_run(tid)
    if r is None:
        return _rpc_error_response(req_id,
                                    wire.wire_replay_refused("not_found"))
    if r.state in wire.TERMINAL_STATES:
        return _rpc_error_response(req_id,
                                    wire.wire_replay_refused(
                                        "task_terminal_use_replay"))
    backlog, q = await _kernel.subscribe(tid, since_seq=None)
    return _stream_response(req_id, tid, backlog, q)

_HANDLERS["SubscribeToTask"] = _subscribe_task


# ── ReplayEvents ──

async def _replay_events(params: dict[str, Any], req_id: Any,
                         _gated_key: str | None) -> Response:
    tid = params.get("taskId")
    if not isinstance(tid, str) or not tid:
        raise _ParamError("taskId is required (string)")
    since_seq = params.get("sinceSeq", 0)
    if not isinstance(since_seq, int) or isinstance(since_seq, bool):
        raise _ParamError("sinceSeq must be an integer")
    out = await _kernel.replay(tid, since_seq)
    if out is None:
        r = _kernel.get_run(tid)
        reason = ("cache_limit" if r is not None and r.cache_limit
                  else "not_found_or_expired")
        return _rpc_error_response(req_id, wire.wire_replay_refused(reason))
    backlog, q = out
    return _stream_response(req_id, tid, backlog, q)

_HANDLERS["ReplayEvents"] = _replay_events


# ── Extensions: InjectContext / SubscribeToContext ──

_INJECT_FORWARD_TIMEOUT_SECONDS = 30.0


async def _forward_engine_inject(ctx: str, text: str) -> None:
    """R12-6/R13-1/R13-4: write the message into the engine's real session.

    Preferred path is the ChatService plugin ``inject`` — the exact call
    ws_server's _handle_chat_inject makes (``chat.inject(session_key=...,
    message=<str>, label=..., auth=...)``; both engine adapters take the
    message as a plain string — claude_code_gateway does
    ``String(params.message)``, a structured object would corrupt the
    transcript). Engines without ``inject`` (openclaw legacy) are forwarded
    through the relay with the same string shape.

    Raises:
      InjectForwardRejected — the engine explicitly rejected the write.
      asyncio.TimeoutError    — outcome unknown (engine may have written).
      RuntimeError            — engine/manager/relay surface unavailable.
    """
    from engine.community.manager import EngineManager
    from engine.community.api.transport.a2a.engine_bridge import _chat_plugin

    chat = _chat_plugin()  # unavailable → RuntimeError, callers fail honestly

    # ws_server._chat_plugin_supports_inject parity (MRO __dict__ check).
    supports_inject = any(
        "inject" in ancestor.__dict__ for ancestor in type(chat).__mro__)
    if supports_inject:
        result = await asyncio.wait_for(
            chat.inject(session_key=ctx, message=text, label=None,
                        auth=None),
            timeout=_INJECT_FORWARD_TIMEOUT_SECONDS)
        if not result.get("ok"):
            err = result.get("error") or {}
            raise InjectForwardRejected(
                str(err.get("message", "engine inject rejected"))[:200],
                code=str(err.get("code", "UNKNOWN")))
        return

    relay = EngineManager.get_instance().relay
    if relay is None:
        raise RuntimeError("no relay plugin on active engine")
    response = await asyncio.wait_for(
        relay.forward_request(
            request_id=f"a2a-inject-{uuid.uuid4().hex}",
            method="chat.inject",
            params={"sessionKey": ctx, "message": text},
            auth=None,
            timeout=_INJECT_FORWARD_TIMEOUT_SECONDS,
        ),
        timeout=_INJECT_FORWARD_TIMEOUT_SECONDS)
    if not bool(getattr(response, "ok", False)):
        error = getattr(response, "error", None)
        raise InjectForwardRejected(
            str(getattr(error, "message", "rejected"))[:200],
            code=str(getattr(error, "code", "UNKNOWN")))


class InjectForwardRejected(Exception):
    """Engine returned a structured refusal for the inject write.

    ``code`` carries the engine error code (never parsed from the message —
    structural classification per R14-1). INTERNAL_ERROR is the transport
    marker both ports emit for connect/timeout/serialization failures:
    the write state is UNKNOWN, so the caller must retain the key. Any other
    code is an explicit engine-level refusal (INVALID_REQUEST, session
    policies, …) where the engine never wrote — safe to roll back.
    """

    def __init__(self, message: str, *, code: str = "UNKNOWN"):
        super().__init__(message)
        self.code = code

    @property
    def outcome_unknown(self) -> bool:
        return self.code in _INJECT_TRANSPORT_UNKNOWN_CODES


# Both claude_code and openclaw ports convert every transport-layer
# exception (connect failure, RPC timeout, …) into success/ok=False with
# code "INTERNAL_ERROR" (claude_code/_chat.py:145-149, openclaw/_chat.py:
# 190-278). That marker means "outcome unknowable, the write may have
# landed" — everything else is a deliberate engine refusal.
_INJECT_TRANSPORT_UNKNOWN_CODES = frozenset({"INTERNAL_ERROR"})


async def _inject_context(params: dict[str, Any], rid: Any,
                          gated_key: str | None) -> dict[str, Any]:
    msg = _get_message(params)
    # R13-3: canonical context validated pre-gate; guarded double-check.
    ctx, _ = _canonical_context("InjectContext", params, rid)
    # ctx 已被上面的 canonical 提取校验为非空 str；inject_reserve 只做
    # dict/哈希操作（坏键是 TypeError 不是 ValueError）——不存在可达的
    # ValueError 源，不设死路 try/except。
    reserve = await _kernel.inject_reserve(ctx, msg["messageId"])
    if reserve != "in_progress":
        # duplicate_in_progress / duplicate_accepted / duplicate_unknown
        return {"inject": reserve}

    text = _message_text(msg)
    try:
        await _forward_engine_inject(ctx, text)
    except asyncio.CancelledError:
        # R14-2: the in-flight operation was cancelled — the engine may
        # have written (result unknowable). Mark unknown, then preserve
        # the cancellation propagation semantics.
        _kernel.inject_unknown(ctx, msg["messageId"])
        raise
    except InjectForwardRejected as e:
        if e.outcome_unknown:
            # R14-1: transport-layer marker (INTERNAL_ERROR) — the port
            # swallowed a connect/timeout failure; the write MAY have
            # landed. Retain the key as unknown; same-key retry must not
            # blindly re-write.
            _kernel.inject_unknown(ctx, msg["messageId"])
            return {"error": {"code": -32000,
                              "message": "inject outcome unknown",
                              "data": {"reason": "unknown_outcome"}}}
        _kernel.inject_rollback(ctx, msg["messageId"])   # retry allowed
        return {"error": {"code": -32000,
                          "message": str(e)[:200],
                          "data": {"reason": "inject_forward_failed"}}}
    except asyncio.TimeoutError:
        # Ambiguous: engine may have written — key retained as unknown;
        # same-key retries get duplicate_unknown; a new operation uses a
        # new messageId.
        _kernel.inject_unknown(ctx, msg["messageId"])
        return {"error": {"code": -32000, "message": "inject outcome unknown",
                          "data": {"reason": "unknown_outcome"}}}
    except RuntimeError as e:
        _kernel.inject_rollback(ctx, msg["messageId"])   # R13-4: honest fail
        return {"error": {"code": -32000, "message": str(e)[:200],
                          "data": {"reason": "inject_target_unavailable"}}}

    _kernel.inject_commit(ctx, text, msg["messageId"])   # broadcast AFTER ok
    return {"inject": "accepted"}

_HANDLERS["InjectContext"] = _inject_context


# ── E4: ResolveInteraction — HITL same-run resolve closed loop ──────────────

class _ResolveRejected(Exception):
    """Engine deliberately refused the resolve decision.

    ``code`` carries the engine error code (structural classification —
    R17-3: real port contract is ``{success, payload|error}``; ``success``
    is the判定 field, not ``ok``); INTERNAL_ERROR 保持 R14 的"结果不可知"。
    """

    def __init__(self, message: str, *, code: str = "UNKNOWN"):
        super().__init__(message)
        self.code = code


class _ResolveUnsupported(Exception):
    """R7-F3: active engine lacks the resolve-contract method for this kind.

    The three ``resolve_*`` methods are Claude Code-specific extensions over
    the ``ChatService`` Protocol — OpenClawChatAdapter has no equivalents
    (its approval resolution rides RelayService.forward_request instead).
    Gate per-kind BEFORE dispatch so the failure surfaces as a structured
    ``resolve_unsupported`` JSON-RPC error, never as a bare AttributeError
    leaking through the handler (HTTP 500 with the approval left pending).
    """


# R7-F3：resolve kind → 所需的 ChatService 契约方法（None kind = ask_user
# 缺省分支，与 _forward_resolve 的分发梯子同源）。
_KIND_RESOLVE_METHOD = {
    "exec": "resolve_exec_approval",
    "ask_user": "resolve_interaction",
    None: "resolve_interaction",
    "mode_switch": "resolve_mode_transition",
}


def _engine_resolve_supported() -> bool:
    """P0/stage2 §5：resolve 声明真值——活动引擎 ChatService 实现
    `_KIND_RESOLVE_METHOD.values()` 全部三方法才为 True。

    与 `_forward_resolve` 运行期门控同源（同一词汇表），部分支持=不
    声明，免于 "card 声明≠实际能力"；取插件失败按不声明处理（保守）。
    属性访问抛错（奇异插件）同样降级为不声明——card 端点永可答，
    不因真值检查 500。请求时计算、无缓存——声明与实现不漂移。"""
    try:
        from engine.community.api.transport.a2a.engine_bridge import _chat_plugin
        chat = _chat_plugin()
        return all(getattr(chat, m, None) is not None
                   for m in set(_KIND_RESOLVE_METHOD.values()))
    except Exception:
        return False


def _validated_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    """resolution 载荷的边界规范化（PR #2743 R3-1）。

    类型不符 = 边界错误（_ParamError → 422），不带病下行。ask_user 的
    结构化答案（answers/values/selected_options|selectedOptions）原样
    保留——与 v3 builder（build_interaction_resolve_request）等价，灰度
    切换不得丢失载荷/改写决策。"""
    decision = resolution.get("decision")
    if not isinstance(decision, str) or not decision:
        raise _ParamError("resolution.decision is required")
    out: dict[str, Any] = {"decision": decision}
    kind = resolution.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise _ParamError("resolution.kind must be a string")
    if kind:
        out["kind"] = kind
    for field in ("answer", "message"):
        value = resolution.get(field)
        if value is not None and not isinstance(value, str):
            raise _ParamError(f"resolution.{field} must be a string")
        if value is not None:
            out[field] = value
    for field in ("answers", "values"):
        value = resolution.get(field)
        if value is not None and not isinstance(value, dict):
            raise _ParamError(f"resolution.{field} must be an object")
        if value is not None:
            out[field] = value
    selected = resolution.get("selected_options")
    if selected is None:
        selected = resolution.get("selectedOptions")   # 容忍 v3 wire 形态
    if selected is not None:
        if not isinstance(selected, list) or any(
                not isinstance(opt, (list, tuple))
                or any(not isinstance(s, str) for s in opt)
                for opt in selected):
            raise _ParamError("resolution.selected_options must be a list "
                              "of string lists")
        out["selected_options"] = [list(opt) for opt in selected]
    return out


async def _forward_resolve(*, session_key: str,
                           interaction_id: str,
                           resolution: dict[str, Any]) -> None:
    """E4/16-3: 三类决策分发到 engine adapter 三方法。

    转发键 = BaaS 持有的真实 interaction 标识——即 interaction.requested
    帧上行的引擎侧 id（gateway 生成，如 ``int:<uuid>``）；引擎的 pending
    注册表按它查找。kernel rid 是传输层 id、native run id 是执行标识，
    引擎 resolve 注册表一概不认，严禁替换成二者之一。

    resolution 载荷经 _validated_resolution 规范化：decision 与结构化
    答案全量转发（R3-1——cancel 不得被改写成 submit，answers/values/
    selected_options 不得丢）。

    Raises _ResolveRejected（结构化 code）。"""
    from engine.community.api.transport.a2a.engine_bridge import _chat_plugin

    chat = _chat_plugin()
    kind = resolution.get("kind")
    decision = resolution.get("decision")

    # R7-F3：per-kind 契约能力门控——三类 ``resolve_*`` 是 Claude Code 专属
    # 扩展（ChatService Protocol 之上），OpenClaw 等形态没有（其审批决议走
    # RelayService.forward_request）。硬调只会 AttributeError 泄漏成 HTTP
    # 500、审批静默挂 pending；门控出结构化 resolve_unsupported 供分诊。
    required_method = _KIND_RESOLVE_METHOD.get(kind)
    if required_method is None:
        raise _ParamError(f"unsupported resolution kind: {kind!r}")
    if getattr(chat, required_method, None) is None:
        raise _ResolveUnsupported(
            f"active engine ChatService has no {required_method} "
            f"(resolve contract not implemented for this engine)")

    if kind == "exec":
        coro = chat.resolve_exec_approval(
            session_key=session_key, interaction_id=interaction_id,
            decision=decision, message=resolution.get("message"))
    elif kind in ("ask_user", None):
        coro = chat.resolve_interaction(
            session_key=session_key, interaction_id=interaction_id,
            decision=decision,
            answer=resolution.get("answer"),
            message=resolution.get("message"),
            answers=resolution.get("answers"),
            values=resolution.get("values"),
            selected_options=resolution.get("selected_options"))
    elif kind == "mode_switch":
        coro = chat.resolve_mode_transition(
            session_key=session_key, interaction_id=interaction_id,
            decision=decision)
    else:
        raise _ParamError(f"unsupported resolution kind: {kind!r}")

    result = await asyncio.wait_for(coro, _RESOLVE_FORWARD_TIMEOUT_SECONDS)
    # R17-3: engine/port 的真实契约是 {success, payload|error}——不是 {ok}。
    if not result.get("success"):
        err = result.get("error") or {}
        raise _ResolveRejected(
            str(err.get("message", "engine resolve rejected")),
            code=str(err.get("code", "UNKNOWN")))


_RESOLVE_FORWARD_TIMEOUT_SECONDS = 30.0


async def _resolve_interaction(params: dict[str, Any], req_id: Any,
                               _gated_key: str | None) -> dict[str, Any]:
    """H1/E4: 同 run 决策提交——§8.6 契约三行判定。

    not_found / resolve_conflict / 成功恢复 WORKING（+resolved 事件）。
    """
    interaction_id = params.get("interactionId")
    if not isinstance(interaction_id, str) or not interaction_id:
        raise _ParamError("interactionId is required (string)")
    raw_resolution = params.get("resolution")
    if not isinstance(raw_resolution, dict):
        raise _ParamError("resolution.decision is required")
    resolution = _validated_resolution(raw_resolution)

    located = _kernel.interaction_run(interaction_id)
    if located is None or located[1].state in wire.TERMINAL_STATES:
        return {"error": {"code": -32000, "message": "interaction not found",
                          "data": {"reason": "not_found"}}}
    rid, run = located
    # R4-2: resolvability is judged per-iid (the same run can carry multiple pending items simultaneously — gateway support for concurrent SDK canUseTool);
    # run.state is no longer a proxy gate. The idempotency anchor is preserved: repeat submission of an already-resolved iid → conflict;
    # legacy "resolved → WORKING → repeat submission" also falls into conflict here (missing iid = clear-all outstanding).
    if not _kernel.interaction_pending(rid, interaction_id):
        return {"error": {"code": -32003,
                          "message": "interaction is not pending "
                                     "(already resolved or never requested)",
                          "data": {"reason": "resolve_conflict"}}}

    # R6-2：捕获当前 pending 轮次——RPC 回补的 resolved 只能清这一轮。
    # 等待期间若引擎把旧轮次决议掉且同一 iid 又挂起新轮次,旧回复不
    # 得把新 pending 洗掉。
    generation = _kernel.interaction_generation(interaction_id)

    try:
        await _forward_resolve(session_key=run.context_id,
                               interaction_id=interaction_id,
                               resolution=resolution)
    except _ResolveRejected as e:
        if e.code == "INTERNAL_ERROR":
            # R14 对位：engine 结果不可知——run 状态不回写为 WORKING，
            # 保持 INPUT_REQUIRED（决策未确认；同键重复 resolve 仍 conflict
            # 分支语义——交互仍悬而未决）。
            return {"error": {"code": -32000,
                              "message": "resolve outcome unknown",
                              "data": {"reason": "unknown_outcome"}}}
        return {"error": {"code": -32000, "message": str(e)[:200],
                          "data": {"reason": "resolve_forward_failed"}}}
    except _ResolveUnsupported as e:
        # R7-F3：引擎无 resolve 契约（如 OpenClaw）——决策未被引擎接受：
        # pending 保持、run 不回 WORKING（同 resolve_forward_failed 口径），
        # 调用方按 reason=resolve_unsupported 分诊，而非面对 HTTP 500。
        return {"error": {"code": -32000, "message": str(e)[:200],
                          "data": {"reason": "resolve_unsupported"}}}
    except asyncio.TimeoutError:
        return {"error": {"code": -32000,
                          "message": "resolve outcome unknown (timeout)",
                          "data": {"reason": "unknown_outcome"}}}
    except RuntimeError as e:
        return {"error": {"code": -32000, "message": str(e)[:200],
                          "data": {"reason": "resolve_target_unavailable"}}}

    await _kernel.publish_interaction_resolved(
        rid, {"interactionId": interaction_id,
              "kind": resolution.get("kind"),
              "decision": resolution.get("decision")},
        expected_generation=generation)
    return {"interaction": {"resolved": True, "runId": rid}}


_HANDLERS["ResolveInteraction"] = _resolve_interaction


async def _subscribe_context(params: dict[str, Any], req_id: Any,
                             _gated_key: str | None) -> Response:
    ctx = params.get("contextId")
    if not isinstance(ctx, str) or not ctx:
        raise _ParamError("contextId is required (string)")
    q = _kernel.subscribe_context(ctx)

    async def gen():
        try:
            while True:
                ev = await _get_with_keepalive(q)
                if ev is _KEEPALIVE_TICK:
                    yield _SSE_KEEPALIVE_FRAME
                    continue
                if ev is None:
                    return
                yield _sse(_rpc_response(req_id,
                                          wire.kernel_event_to_wire(ev)))
        finally:
            _kernel.unsubscribe_context(ctx, q)

    return StreamingResponse(gen(), media_type="text/event-stream")

_HANDLERS["SubscribeToContext"] = _subscribe_context


# ── SSE stream generator ──

def _stream_response(req_id: Any, rid: str, backlog: list, q: Any) -> Response:
    async def gen():
        try:
            for e in backlog:
                yield _sse(_rpc_response(req_id,
                                         wire.kernel_event_to_wire(e)))
            while True:
                ev = await _get_with_keepalive(q)
                if ev is _KEEPALIVE_TICK:
                    yield _SSE_KEEPALIVE_FRAME
                    continue
                if ev is None:
                    return
                yield _sse(_rpc_response(req_id,
                                          wire.kernel_event_to_wire(ev)))
                su = (ev.get("ev") or {}).get("statusUpdate") or {}
                if (su.get("metadata") or {}).get("final") == "true":
                    return
        finally:
            _kernel.unsubscribe(rid, q)

    return StreamingResponse(gen(), media_type="text/event-stream")