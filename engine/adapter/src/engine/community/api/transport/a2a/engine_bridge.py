"""Bridge: EngineManager chat dispatch → A2aKernel event stream.

Step-1 discovery (what this bridge actually calls):
  - EngineManager exposes no direct `send_chat`/`dispatch` method. Chat goes
    through the plugin surface: `EngineManager.get_instance().chat.stream(
    ChatRequest(...), auth) -> AsyncIterator[EventFrame]` (ChatService
    Protocol, core/chat/protocol.py) — the same path ws_server.py drives for
    `chat.send`.
  - Engine availability: `manager.chat` raises RuntimeError while no engine
    is active (before `await manager.initialize()`), and `get_instance()`
    can itself fail in non-DI unit-test contexts. When the surface does not
    resolve, the bridge degrades to echo mode instead of leaving the run
    hanging.
  - Event vocabulary: EventFrame.payload is the ws_server chat envelope —
    `{"state": "delta"|"final"|"error"|"aborted", "message": {content
    blocks}, "deltaText": <increment>}` (canonical shape in
    plugins/openclaw/_chat.py).

Dispatch mapping:
  - chat `delta`    → kernel.publish_delta(rid, text)
  - chat `final`    → kernel.publish_final(rid, text)
  - `error`         → complete_run FAILED
  - `aborted`       → complete_run CANCELED
  - stream exhausted without a terminal event → publish_final(accumulated)
    + COMPLETE, so the SSE stream always terminates.

Echo fallback (MVP): context_id doubles as the chat session key
(claude_code composes its own key from agentId/sessionId/userId; openclaw
treats it as opaque) and auth=None — the documented HTTP-path default.
Singlebox verification is the final judge of the real engine integration.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing, nullcontext
from typing import Any

from engine.community.api.transport.a2a.kernel import A2aKernel
from engine.community.core.chat.models import ChatRequest

log = logging.getLogger("engine-a2a-bridge")

_STATE_COMPLETED = "TASK_STATE_COMPLETED"
_STATE_WORKING = "TASK_STATE_WORKING"
_STATE_FAILED = "TASK_STATE_FAILED"
_STATE_CANCELED = "TASK_STATE_CANCELED"

# echo fallback 的一次事件循环让位（把控制权交给 SSE 订阅者先注册）
_ECHO_YIELD_SECONDS = 0.05

# Terminal chat states from the ws_server relay vocabulary.
_TERMINAL_CHAT_STATES = ("final", "error", "aborted")


class _EngineRunFailed(Exception):
    """Chat stream reported `state=error` — run must fail."""


class _EngineRunAborted(Exception):
    """Chat stream reported `state=aborted` — run was cancelled."""


async def execute_and_stream(kernel: A2aKernel, rid: str, context_id: str,
                             text: str,
                             auth: Any = None) -> None:
    """Dispatch through EngineManager and pump events into kernel.

    Guarantees exactly one terminal `complete_run` (SUCCESS unless the engine
    stream signals error/aborted), so SSE subscribers always see the terminal
    marker. `auth` carries the principal extracted at the transport boundary
    (R12-4) — plugins that pool upstream connections per-tenant use it to
    pick the right client.
    """
    log.info("bridge: run=%s context=%s text_len=%d", rid, context_id,
             len(text))
    try:
        await kernel.publish_status(rid, _STATE_WORKING)
    except Exception:
        log.exception("bridge: run %s vanished before dispatch", rid)
        return

    try:
        dispatched = await _dispatch_to_engine(kernel, rid, context_id, text,
                                               auth)
        if not dispatched:
            await _echo_fallback(kernel, rid, text)
        await kernel.complete_run(rid, _STATE_COMPLETED)
    except _EngineRunFailed:
        await _safe_complete(kernel, rid, _STATE_FAILED)
    except _EngineRunAborted:
        await _safe_complete(kernel, rid, _STATE_CANCELED)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("bridge: engine dispatch failed for run %s", rid)
        await _safe_complete(kernel, rid, _STATE_FAILED)


async def _safe_complete(kernel: A2aKernel, rid: str, state: str) -> None:
    try:
        await kernel.complete_run(rid, state)
    except Exception:
        log.exception("bridge: complete_run(%s, %s) failed", rid, state)


async def _dispatch_to_engine(kernel: A2aKernel, rid: str, context_id: str,
                              text: str, auth: Any = None) -> bool:
    """Drive the real ChatService.stream and translate frames.

    Returns True when an engine stream was actually consumed (whatever its
    outcome). Returns False — leaving the run uncompleted — when the engine
    surface is unavailable, so the caller can fall back to echo mode.
    """
    try:
        chat = _chat_plugin()
    except Exception as e:
        log.warning("bridge: engine chat surface unavailable (%s: %s) "
                    "— echo fallback for run %s",
                    type(e).__name__, e, rid)
        return False

    request = ChatRequest(
        userId="",
        agentId="",
        query=text,
        sessionId=context_id,
        aliveTime=None,
        extraParams=None,
    )

    accumulated: list[str] = []
    stream = chat.stream(request, auth=auth)
    # ChatService Protocol allows both shapes: async-generator functions
    # (calling yields an async iterator directly — the bundled engines) and
    # coroutines returning an AsyncIterator. Normalize before iterating.
    if not hasattr(stream, "__aiter__"):
        stream = await stream
    # aclosing() only applies to async generators; bare iterators lack
    # aclose and must not be wrapped.
    closing = aclosing(stream) if hasattr(stream, "aclose") else nullcontext()
    async with closing:
        async for frame in stream:
            payload = frame.payload if isinstance(frame.payload, dict) else {}
            event = getattr(frame, "event", "")
            state = payload.get("state", "")
            run_id = payload.get("runId")

            # R12-3: engine-callback inject frames (runId=inject-*) are
            # session-level fanout, never this run's stream or terminal —
            # ws_server keeps the same exception at :1746-1763.
            if isinstance(run_id, str) and run_id.startswith("inject-"):
                inject_text = _payload_text(payload, state)
                if inject_text:
                    kernel.publish_engine_inject(context_id, inject_text,
                                                 run_id)
                continue

            if event == "chat" or state in _TERMINAL_CHAT_STATES:
                if state == "delta":
                    chunk = _payload_text(payload, state)
                    if chunk:
                        accumulated.append(chunk)
                        await kernel.publish_delta(rid, chunk)
                elif state == "final":
                    final_text = _payload_text(payload, state) \
                        or "".join(accumulated)
                    if final_text:
                        await kernel.publish_final(rid, final_text)
                    # Terminal: let the outer handler complete the run.
                    return True
                elif state == "error":
                    message = payload.get("errorMessage")
                    log.error("bridge: chat stream error for run %s: %s",
                              rid, message)
                    raise _EngineRunFailed(str(message or "engine error"))
                elif state == "aborted":
                    raise _EngineRunAborted()

    if accumulated:
        # Stream exhausted without a terminal frame (e.g. cached-request
        # replays closed early, upstream dropped the final) — reaching here
        # means no "final" state was seen (final returns at the loop above).
        # Publish the accumulated text as the final artifact so replay stays
        # coherent.
        await kernel.publish_final(rid, "".join(accumulated))
    return True


def _chat_plugin() -> Any:
    """Resolve the active engine's ChatService via EngineManager."""
    from engine.community.manager import EngineManager

    return EngineManager.get_instance().chat


def _payload_text(payload: dict[str, Any], state: str = "final") -> str:
    """Extract text from a chat EventFrame payload.

    R12-2: delta frames carry the increment in ``deltaText`` (openclaw) or
    ``delta`` (claude_code gateway) while ``message.content`` holds the
    cumulative snapshot (orchestrator-bridge.ts:198-210: delta=event.delta,
    content=event.fullText). Reading message.content on a delta frame would
    double-append ("你", "你好" → "你你好"). On delta frames the increment
    fields win and the snapshot is never consulted; on final frames the
    snapshot is the authoritative full text.
    """
    if state == "delta":
        for field in ("deltaText", "delta"):
            inc = payload.get(field)
            if isinstance(inc, str) and inc:
                return inc
        return ""  # 缺增量字段：宁可跳过该帧，不误读累计快照

    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    # fallback：非 delta 终态若没有 message，尝试增量字段兜底
    for field in ("deltaText", "delta"):
        inc = payload.get(field)
        if isinstance(inc, str) and inc:
            return inc
    return ""


async def _echo_fallback(kernel: A2aKernel, rid: str, text: str) -> None:
    """MVP echo: prove the kernel→SSE pipeline without a live engine.

    Replaced implicitly once EngineManager resolves (real dispatch path); kept
    as the no-engine behavior so unit-test and pre-initialize singlebox
    contexts still stream a well-formed run to completion.
    """
    await asyncio.sleep(_ECHO_YIELD_SECONDS)  # yield to the event loop once
    await kernel.publish_delta(rid, f"[echo] {text[:200]}")
    await kernel.publish_final(rid, f"[echo-final] {text[:500]}")


__all__ = ["execute_and_stream"]
