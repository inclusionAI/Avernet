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
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi_injector import Injected
from starlette.responses import JSONResponse, Response, StreamingResponse

from engine.community.api.transport.a2a import wire
from engine.community.api.transport.a2a.card import EXTENSION_URIS, make_agent_card
from engine.community.api.transport.a2a.engine_bridge import execute_and_stream
from engine.community.api.transport.a2a.kernel import A2aKernel, KeyConflictError
from engine.community.plugin_api.auth_gate.protocol import AuthGateService

router = APIRouter()

# Module-level kernel singleton (one per FastAPI process).
_kernel = A2aKernel()

# Strong references to in-flight engine_bridge tasks: asyncio only weakly
# tracks tasks, so an unreferenced fire-and-forget task can be garbage
# collected mid-run and silently kill the event stream.
_BRIDGE_TASKS: set[asyncio.Task] = set()

# Methods that can trigger engine execution — gated exactly like ws_server's
# handling of chat.send / chat.inject.
_GATED_METHODS = frozenset({"SendMessage", "SendStreamingMessage",
                            "InjectContext", "ResolveInteraction"})

_HANDLERS: dict[str, Any] = {}

# HTTP header the WS surface reads the same principal from (frame param
# x-iam-token); A2A HTTP callers pass it as a request header.
_IAM_TOKEN_HEADER = "x-iam-token"


def _sse(data: Any) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


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
    return make_agent_card(get_local_base_url())


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

def _ensure_dispatched(rid: str, ctx: str, text: str, created: bool) -> None:
    """Exactly-once engine dispatch across both send entries: D14 "FIRST |
    absent | register and begin execution". A dedup hit (created=False)
    attaches to the existing stream instead of dispatching again."""
    if not created:
        return
    task = asyncio.create_task(execute_and_stream(_kernel, rid, ctx, text))
    _BRIDGE_TASKS.add(task)
    task.add_done_callback(_BRIDGE_TASKS.discard)


# ── SendMessage ──

async def _send_message(params: dict[str, Any], rid: Any,
                        gated_key: str | None) -> dict[str, Any]:
    msg = _get_message(params)
    ctx = msg.get("contextId")
    if not isinstance(ctx, str) or not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.send_message(ctx, _message_text(msg),
                                     gated_key or msg["messageId"],
                                     _retry_flag(params))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return {"error": wire.wire_unknown_outcome()}
    run_rid = out["task"]["id"]
    created = out["task"]["metadata"]["created"]
    # R12-1: non-streaming FIRST must start execution too — the run cannot
    # be left dangling at SUBMITTED. Dedup hits dispatch nothing (C1).
    _ensure_dispatched(run_rid, ctx, _message_text(msg), created)
    return out

_HANDLERS["SendMessage"] = _send_message


# ── SendStreamingMessage ──

async def _send_streaming(params: dict[str, Any], req_id: Any,
                          gated_key: str | None) -> Response:
    msg = _get_message(params)
    ctx = msg.get("contextId")
    if not isinstance(ctx, str) or not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.open_run(ctx, gated_key or msg["messageId"],
                                 _retry_flag(params),
                                 _digest(_message_text(msg)))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return _rpc_error_response(req_id, wire.wire_unknown_outcome())
    rid, created = out

    _ensure_dispatched(rid, ctx, _message_text(msg), created)

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
    try:
        reserve = await _kernel.inject_reserve(ctx, msg["messageId"])
    except ValueError:
        raise _ParamError("contextId is required (string)")
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


async def _forward_resolve(*, session_key: str, run: Any,
                           resolution: dict[str, Any]) -> None:
    """E4/16-3: 三类决策分发到 engine adapter 三方法（16-3b：native_run_id）。

    Raises _ResolveRejected（结构化 code）——Internal ids检查先行。"""
    from engine.community.api.transport.a2a.engine_bridge import _chat_plugin

    native = run.native_run_id
    if not isinstance(native, str) or not native:
        raise RuntimeError(
            "native run id unavailable (run has no engine frames yet)")
    chat = _chat_plugin()
    kind = resolution.get("kind")
    decision = resolution.get("decision")

    if kind == "exec":
        coro = chat.resolve_exec_approval(
            session_key=session_key, run_id=native, decision=decision,
            message=resolution.get("message"))
    elif kind in ("ask_user", None):
        coro = chat.resolve_interaction(
            session_key=session_key, run_id=native,
            response=resolution.get("answer") or resolution.get("message"))
    elif kind == "mode_switch":
        coro = chat.resolve_mode_transition(
            session_key=session_key, run_id=native, decision=decision)
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
    resolution = params.get("resolution")
    if not isinstance(resolution, dict) or not resolution.get("decision"):
        raise _ParamError("resolution.decision is required")

    located = _kernel.interaction_run(interaction_id)
    if located is None or located[1].state in wire.TERMINAL_STATES:
        return {"error": {"code": -32000, "message": "interaction not found",
                          "data": {"reason": "not_found"}}}
    rid, run = located
    if run.state != "TASK_STATE_INPUT_REQUIRED":
        return {"error": {"code": -32003,
                          "message": f"run not awaiting input "
                                     f"(state={run.state})",
                          "data": {"reason": "resolve_conflict"}}}

    try:
        await _forward_resolve(session_key=run.context_id, run=run,
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
              "decision": resolution.get("decision")})
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
                ev = await q.get()
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
                ev = await q.get()
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