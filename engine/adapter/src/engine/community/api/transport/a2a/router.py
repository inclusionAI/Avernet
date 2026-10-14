"""A2A v1.0 JSON-RPC binding — FastAPI router.

Thin shell: request parse → kernel call → wire convert → Response.
No state (kernel module owns it all).

Envelope discipline (JSON-RPC 2.0):
  - every response/error carries the request's own `id` (never messageId),
  - handlers return plain result dicts; `rpc()` wraps them,
  - SSE generators close over the request id for stream event envelopes,
  - boundary validation: malformed JSON → -32700, non-object body → -32600,
    missing message/messageId → -32602 (never a naked 500).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from engine.community.api.transport.a2a import wire
from engine.community.api.transport.a2a.card import make_agent_card
from engine.community.api.transport.a2a.engine_bridge import execute_and_stream
from engine.community.api.transport.a2a.kernel import A2aKernel, KeyConflictError

router = APIRouter()

# Module-level kernel singleton (one per FastAPI process).
_kernel = A2aKernel()

# Strong references to in-flight engine_bridge tasks: asyncio only weakly
# tracks tasks, so an unreferenced fire-and-forget task can be garbage
# collected mid-run and silently kill the event stream.
_BRIDGE_TASKS: set[asyncio.Task] = set()

_HANDLERS: dict[str, Any] = {}


def _sse(data: Any) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _rpc_response(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error_response(rid: Any, error: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": error})


@router.get("/.well-known/agent-card.json")
async def agent_card() -> dict[str, Any]:
    from engine.community.config import get_local_base_url
    return make_agent_card(get_local_base_url())


@router.post("/rpc")
async def rpc(request: Request) -> Response:
    # ── boundary validation (H2): malformed input never becomes a naked 500 ──
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
    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_error_response(rid, {"code": -32602,
                                          "message": "Invalid params"})

    handler = _HANDLERS.get(method)
    if handler is None:
        return _rpc_error_response(rid, {"code": -32601,
                                          "message": f"Method not found: {method}"})
    try:
        outcome = await handler(params, rid)
    except KeyConflictError:
        return _rpc_error_response(rid, {"code": -32003,
                                          "data": {"reason": "key_conflict"}})
    except ValueError as e:
        msg = str(e)
        if "contextId" in msg:
            return _rpc_error_response(rid, wire.wire_context_id_required())
        return _rpc_error_response(rid, {"code": -32602, "message": msg})
    except LookupError:
        return _rpc_error_response(rid, wire.wire_replay_refused("not_found"))

    if isinstance(outcome, Response):
        return outcome
    if isinstance(outcome, dict) and "error" in outcome:
        return _rpc_error_response(rid, outcome["error"])
    return _rpc_response(rid, outcome)


# ── Retry flag (D14 §8.5.2: explicit rejection on malformed flag — H3) ──

class _RetryFlagError(ValueError):
    """avernet.retryOnly is present but not a boolean — reject, never
    silently coerce (string "false" truthy would route FIRST→retry_only)."""


def _retry_flag(params: dict[str, Any]) -> str:
    meta = params.get("metadata")
    if not isinstance(meta, dict) or "avernet.retryOnly" not in meta:
        return "first"
    val = meta["avernet.retryOnly"]
    if not isinstance(val, bool):
        raise _RetryFlagError("avernet.retryOnly must be boolean")
    return "retry_only" if val else "first"


def _message_text(msg: dict[str, Any]) -> str:
    return "".join(p.get("text", "") for p in msg.get("parts", []))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _get_message(params: dict[str, Any]) -> dict[str, Any]:
    """Extract + validate the `message` member (H2: messageId is required)."""
    msg = params.get("message")
    if not isinstance(msg, dict):
        raise _RetryFlagError("params.message is required and must be an object")
    if not msg.get("messageId"):
        raise _RetryFlagError("message.messageId is required")
    return msg


# ── SendMessage ──

async def _send_message(params: dict[str, Any], _rid: Any) -> dict[str, Any]:
    msg = _get_message(params)
    ctx = msg.get("contextId")
    if not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.send_message(ctx, _message_text(msg),
                                     msg["messageId"],
                                     _retry_flag(params))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return {"error": wire.wire_unknown_outcome()}
    return out

_HANDLERS["SendMessage"] = _send_message


# ── SendStreamingMessage ──

async def _send_streaming(params: dict[str, Any], req_id: Any) -> Response:
    msg = _get_message(params)
    ctx = msg.get("contextId")
    if not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.open_run(ctx, msg["messageId"],
                                 _retry_flag(params),
                                 _digest(_message_text(msg)))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        # H1: error envelope must carry the request id so D14 clients can
        # correlate the unknown-outcome signal with the in-flight request.
        return _rpc_error_response(req_id, wire.wire_unknown_outcome())
    rid, created = out

    # C1 (D14 "FIRST|exists|dedup" row): only a newly-created run dispatches
    # the engine. A same-key re-submit (e.g. response lost, client retries)
    # attaches to the existing stream — the engine executes exactly once.
    if created:
        # Dispatch through the engine bridge in the background; the SSE
        # generator below subscribes to the kernel and relays whatever the
        # bridge publishes (delta/final/status events + terminal marker).
        task = asyncio.create_task(execute_and_stream(
            _kernel, rid, ctx, _message_text(msg)))
        _BRIDGE_TASKS.add(task)
        task.add_done_callback(_BRIDGE_TASKS.discard)

    backlog, q = await _kernel.subscribe(rid, since_seq=0)
    return _stream_response(req_id, rid, backlog, q)

_HANDLERS["SendStreamingMessage"] = _send_streaming


# ── GetTask (design §8.1 third-route fallback after replay refusal) ──

async def _get_task(params: dict[str, Any], req_id: Any) -> dict[str, Any]:
    tid = params.get("taskId", "")
    r = _kernel.get_run(tid)
    if r is None:
        return {"error": {"code": -32000,
                          "message": "task not found or expired",
                          "data": {"reason": "not_found_or_expired"}}}
    return {"task": {"id": r.run_id, "contextId": r.context_id,
                      "status": {"state": r.state}, "metadata": {}}}

_HANDLERS["GetTask"] = _get_task


# ── SubscribeToTask ──

async def _subscribe_task(params: dict[str, Any], req_id: Any) -> Response:
    tid = params.get("taskId", "")
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

async def _replay_events(params: dict[str, Any], req_id: Any) -> Response:
    tid = params.get("taskId", "")
    out = await _kernel.replay(tid, params.get("sinceSeq", 0))
    if out is None:
        r = _kernel.get_run(tid)
        reason = ("cache_limit" if r is not None and r.cache_limit
                  else "not_found_or_expired")
        return _rpc_error_response(req_id, wire.wire_replay_refused(reason))
    backlog, q = out
    return _stream_response(req_id, tid, backlog, q)

_HANDLERS["ReplayEvents"] = _replay_events


# ── Extensions ──

async def _inject_context(params: dict[str, Any], _rid: Any) -> dict[str, Any]:
    ctx = params.get("contextId", "")
    msg = _get_message(params)
    status = await _kernel.inject(ctx, _message_text(msg), msg["messageId"])
    return {"inject": status}

_HANDLERS["InjectContext"] = _inject_context


async def _subscribe_context(params: dict[str, Any], req_id: Any) -> Response:
    ctx = params.get("contextId", "")
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