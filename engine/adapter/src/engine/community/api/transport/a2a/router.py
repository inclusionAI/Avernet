"""A2A v1.0 JSON-RPC binding — FastAPI router.

Thin shell: request parse → kernel call → wire convert → Response.
No state (kernel module owns it all).
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

_PING = f"data: {json.dumps({'jsonrpc': '2.0', 'method': 'ping', 'params': {}})}\n\n"


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
    body = await request.json()
    rid = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    handler = _HANDLERS.get(method)
    if handler is None:
        return _rpc_error_response(rid, {"code": -32601,
                                          "message": f"Method not found: {method}"})
    try:
        outcome = await handler(params)
    except KeyConflictError:
        return _rpc_error_response(rid, {"code": -32003,
                                          "data": {"reason": "key_conflict"}})
    except ValueError as e:
        msg = str(e)
        if "contextId" in msg:
            return _rpc_error_response(rid, wire.wire_context_id_required())
        return _rpc_error_response(rid, {"code": -32602, "message": msg})

    if isinstance(outcome, Response):
        return outcome
    if isinstance(outcome, dict) and "error" in outcome:
        return _rpc_error_response(rid, outcome["error"])
    return _rpc_response(rid, outcome)


# ── Retry flag ──

def _retry_flag(params: dict[str, Any]) -> str:
    meta = params.get("metadata")
    if not meta or "avernet.retryOnly" not in meta:
        return "first"
    return "retry_only" if meta["avernet.retryOnly"] else "first"


def _message_text(msg: dict[str, Any]) -> str:
    return "".join(p.get("text", "") for p in msg.get("parts", []))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ── SendMessage ──

async def _send_message(params: dict[str, Any]) -> dict[str, Any]:
    msg = params["message"]
    ctx = msg.get("contextId")
    if not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.send_message(ctx, _message_text(msg),
                                     msg.get("messageId", ""),
                                     _retry_flag(params))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return {"error": wire.wire_unknown_outcome()}
    return out

_HANDLERS["SendMessage"] = _send_message


# ── SendStreamingMessage ──

async def _send_streaming(params: dict[str, Any]) -> Response:
    msg = params["message"]
    ctx = msg.get("contextId")
    if not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.open_run(ctx, msg.get("messageId", ""),
                                 _retry_flag(params),
                                 _digest(_message_text(msg)))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return _rpc_error_response(msg.get("messageId"),
                                    wire.wire_unknown_outcome())
    rid, _created = out

    # Dispatch through the engine bridge in the background; the SSE
    # generator below subscribes to the kernel and relays whatever the
    # bridge publishes (delta/final/status events + terminal marker).
    task = asyncio.create_task(execute_and_stream(
        _kernel, rid, ctx, _message_text(msg)))
    _BRIDGE_TASKS.add(task)
    task.add_done_callback(_BRIDGE_TASKS.discard)

    backlog, q = await _kernel.subscribe(rid, since_seq=0)
    return _stream_response(rid, backlog, q)

_HANDLERS["SendStreamingMessage"] = _send_streaming


# ── SubscribeToTask ──

async def _subscribe_task(params: dict[str, Any]) -> Response:
    tid = params.get("taskId", "")
    r = _kernel.get_run(tid)
    if r is None:
        return _rpc_error_response(params.get("taskId"),
                                    wire.wire_replay_refused("not_found"))
    if r.state in wire.TERMINAL_STATES:
        return _rpc_error_response(params.get("taskId"),
                                    wire.wire_replay_refused(
                                        "task_terminal_use_replay"))
    backlog, q = await _kernel.subscribe(tid, since_seq=None)
    return _stream_response(tid, backlog, q)

_HANDLERS["SubscribeToTask"] = _subscribe_task


# ── ReplayEvents ──

async def _replay_events(params: dict[str, Any]) -> Response:
    tid = params.get("taskId", "")
    out = await _kernel.replay(tid, params.get("sinceSeq", 0))
    if out is None:
        r = _kernel.get_run(tid)
        reason = ("cache_limit" if r is not None and r.cache_limit
                  else "not_found_or_expired")
        return _rpc_error_response(params.get("taskId"),
                                    wire.wire_replay_refused(reason))
    backlog, q = out
    return _stream_response(tid, backlog, q)

_HANDLERS["ReplayEvents"] = _replay_events


# ── Extensions ──

async def _inject_context(params: dict[str, Any]) -> dict[str, Any]:
    ctx = params.get("contextId", "")
    msg = params["message"]
    status = await _kernel.inject(ctx, _message_text(msg),
                                  msg.get("messageId", ""))
    return {"inject": status}

_HANDLERS["InjectContext"] = _inject_context


async def _subscribe_context(params: dict[str, Any]) -> Response:
    ctx = params.get("contextId", "")
    q = _kernel.subscribe_context(ctx)

    async def gen():
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield _sse(_rpc_response(None,
                                          wire.kernel_event_to_wire(ev)))
        finally:
            _kernel.unsubscribe_context(ctx, q)

    return StreamingResponse(gen(), media_type="text/event-stream")

_HANDLERS["SubscribeToContext"] = _subscribe_context


# ── SSE stream generator ──

def _stream_response(rid: str, backlog: list,
                     q: Any) -> Response:
    async def gen():
        try:
            for e in backlog:
                yield _sse(_rpc_response(None, wire.kernel_event_to_wire(e)))
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield _sse(_rpc_response(None,
                                          wire.kernel_event_to_wire(ev)))
                su = (ev.get("ev") or {}).get("statusUpdate") or {}
                if (su.get("metadata") or {}).get("final") == "true":
                    return
        finally:
            _kernel.unsubscribe(rid, q)

    return StreamingResponse(gen(), media_type="text/event-stream")
