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
    resolve, the bridge fails the run honestly — FAILED terminal + a
    structured ``ENGINE_UNAVAILABLE`` detail (vocabulary aligned with the
    inject surface's ``inject_target_unavailable``) — instead of leaving
    the run hanging or fabricating a reply (the old echo fallback dressed
    engine-downtime up as a successfully answered run; removed 2026-10-15).
  - Event vocabulary: EventFrame.payload is the ws_server chat envelope —
    `{"state": "delta"|"final"|"error"|"aborted", "message": {content
    blocks}, "deltaText": <increment>}` (canonical shape in
    plugins/openclaw/_chat.py).

Dispatch mapping:
  - chat `delta`    → kernel.publish_delta(rid, text)
  - chat `final`    → kernel.publish_final(rid, text)
  - `error`         → complete_run FAILED (+ structured error detail)
  - `aborted`       → complete_run CANCELED
  - stream exhausted without a terminal event → complete_run FAILED with a
    structured ``STREAM_TERMINATED_WITHOUT_FINAL`` detail — never fake
    success (partial deltas were already streamed and stay in the
    snapshot), so the SSE stream always ends in an explicit terminal.

Session key: context_id doubles as the chat session key (claude_code
composes its own key from agentId/sessionId/userId; openclaw treats it as
opaque) and auth=None — the documented HTTP-path default. Singlebox
verification is the final judge of the real engine integration.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import aclosing, nullcontext
from typing import Any

from engine.community.api.transport.a2a.kernel import A2aKernel
from engine.community.core.chat.models import ChatRequest

log = logging.getLogger("engine-a2a-bridge")

_STATE_COMPLETED = "TASK_STATE_COMPLETED"
_STATE_WORKING = "TASK_STATE_WORKING"
_STATE_FAILED = "TASK_STATE_FAILED"
_STATE_CANCELED = "TASK_STATE_CANCELED"

# Engine-surface unavailability (manager/plugin raise, or chat surface
# resolves to None). Vocabulary aligned with the inject surface's
# `inject_target_unavailable` honest-failure naming.
_ERROR_ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"

# Terminal chat states from the ws_server relay vocabulary.
_TERMINAL_CHAT_STATES = ("final", "error", "aborted")


class _EngineRunFailed(Exception):
    """Chat stream reported `state=error` (or died without a terminal) —
    run must fail. ``detail`` becomes the terminal statusUpdate metadata so
    the A2A consumer gets an explainable error (BaaS reads ``errorMessage``
    there — _bot_a2a_client._on_statusUpdate)."""

    def __init__(self, message: str,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class _EngineRunAborted(Exception):
    """Chat stream reported `state=aborted` — run was cancelled."""


async def execute_and_stream(kernel: A2aKernel, rid: str, context_id: str,
                             text: str,
                             auth: Any = None,
                             alive_ms: int | None = None) -> None:
    """Dispatch through EngineManager and pump events into kernel.

    Guarantees exactly one terminal `complete_run` (SUCCESS only when the
    engine stream produced a real final; error/aborted/unavailable end in
    FAILED/CANCELED with structured detail), so SSE subscribers always see
    the terminal marker. `auth` carries the principal extracted at the
    transport boundary (R12-4) — plugins that pool upstream connections
    per-tenant use it to pick the right client.
    """
    log.info("bridge: run=%s context=%s text_len=%d", rid, context_id,
             len(text))
    try:
        await kernel.publish_status(rid, _STATE_WORKING)
    except Exception:
        log.exception("bridge: run %s vanished before dispatch", rid)
        return

    try:
        await _dispatch_to_engine(kernel, rid, context_id, text, auth,
                                  alive_ms)
        await kernel.complete_run(rid, _STATE_COMPLETED)
    except _EngineRunFailed as exc:
        await _safe_complete(kernel, rid, _STATE_FAILED, exc.detail)
    except _EngineRunAborted:
        await _safe_complete(kernel, rid, _STATE_CANCELED)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("bridge: engine dispatch failed for run %s", rid)
        await _safe_complete(kernel, rid, _STATE_FAILED)


async def _safe_complete(kernel: A2aKernel, rid: str, state: str,
                         detail: dict[str, Any] | None = None) -> None:
    try:
        await kernel.complete_run(rid, state, detail)
    except Exception:
        log.exception("bridge: complete_run(%s, %s) failed", rid, state)


async def _dispatch_to_engine(kernel: A2aKernel, rid: str, context_id: str,
                              text: str, auth: Any = None,
                              alive_ms: int | None = None) -> None:
    """Drive the real ChatService.stream and translate frames.

    Returns only when an engine stream was actually consumed to its
    terminal (whatever its outcome). Raises ``_EngineRunFailed`` with a
    structured ``ENGINE_UNAVAILABLE`` detail when the engine surface does
    not resolve — never fabricate a reply for an unavailable engine.
    """
    try:
        chat = _chat_plugin()
    except Exception as e:
        log.warning("bridge: engine chat surface unavailable (%s: %s) "
                    "— failing run %s",
                    type(e).__name__, e, rid)
        raise _EngineRunFailed(
            f"engine chat surface unavailable ({type(e).__name__}: {e})",
            detail={
                "errorCode": _ERROR_ENGINE_UNAVAILABLE,
                "errorMessage": (
                    f"The engine chat surface is unavailable "
                    f"({type(e).__name__}: {e}); the run cannot be "
                    f"dispatched."
                ),
            },
        ) from e

    if chat is None:
        log.warning("bridge: engine chat surface resolved to None — "
                    "failing run %s", rid)
        raise _EngineRunFailed(
            "engine chat surface resolved to None",
            detail={
                "errorCode": _ERROR_ENGINE_UNAVAILABLE,
                "errorMessage": ("The engine chat surface is unavailable "
                                 "(no active chat plugin); the run cannot "
                                 "be dispatched."),
            },
        )

    request = ChatRequest(
        userId="",
        agentId="",
        query=text,
        sessionId=context_id,
        # R4-3：客户端超时对位 v3——v3 是 ws_server 的
        # aliveTime=timeout_ms，None = 未声明（引擎默认）。
        aliveTime=alive_ms,
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

            # E4-3b: engine 原生 runId 首见即登记（resolve 转发用它——
            # kernel rid 是传输层 id，engine 不认识）。
            if isinstance(run_id, str) and run_id and \
                    not run_id.startswith("inject-"):
                kernel.note_native_run_id(rid, run_id)

            # R12-3: engine-callback inject frames (runId=inject-*) are
            # session-level fanout, never this run's stream or terminal —
            # ws_server keeps the same exception at :1746-1763.
            if isinstance(run_id, str) and run_id.startswith("inject-"):
                inject_text = _payload_text(payload, state)
                if inject_text:
                    kernel.publish_engine_inject(context_id, inject_text,
                                                 run_id)
                continue

            # ── R17-2/16-6: non-chat engine frames — lossless routing with
            # original event names. The chat branch below stays untouched. ──
            if event != "chat" and state not in _TERMINAL_CHAT_STATES:
                if state == "interaction.requested" or \
                        event == "interaction.requested":
                    iid = payload.get("interactionId") or payload.get("id")
                    if not isinstance(iid, str) or not iid:
                        iid = f"int-{uuid.uuid4().hex[:8]}"
                    await kernel.publish_interaction_requested(
                        rid, iid, payload)
                    continue
                if state == "interaction.resolved" or \
                        event == "interaction.resolved":
                    await kernel.publish_interaction_resolved(rid, payload)
                    continue
                if state in ("approval_requested", "approval_resolved"):
                    iid = payload.get("approvalId") or payload.get("id")
                    if not isinstance(iid, str) or not iid:
                        iid = f"apr-{uuid.uuid4().hex[:8]}"
                    if state == "approval_requested":
                        await kernel.publish_interaction_requested(
                            rid, iid, payload)
                    else:
                        await kernel.publish_interaction_resolved(rid, payload)
                    continue
                if payload:
                    # agent & mode_transition.resolved 共走 lossless 通道，
                    # original_event 决定 BaaS 消费者回调路由（R17-2）。
                    await kernel.publish_agent_progress(
                        rid, payload, event_name=event)
                    continue
                log.debug("bridge: dropping empty frame event=%s state=%s",
                          event, state)
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
                    return
                elif state == "error":
                    message = payload.get("errorMessage")
                    log.error("bridge: chat stream error for run %s: %s",
                              rid, message)
                    raise _EngineRunFailed(
                        str(message or "engine error"),
                        detail={
                            "errorCode": str(
                                payload.get("errorCode")
                                or "ENGINE_STREAM_ERROR"),
                            "errorMessage": str(message or "engine error"),
                            "engine_frame": dict(payload),
                        },
                    )
                elif state == "aborted":
                    raise _EngineRunAborted()

    # Stream exhausted without a terminal frame — upstream died silently
    # (cluster E2E 2026-10-15: model-provider retry ladder exhausted, the
    # stream simply ended with neither final nor error; reproduced twice).
    # Never dress that as success: partial deltas were already streamed and
    # stay in the snapshot; the run must end FAILED with a structured,
    # explainable error so subscribers don't hang and don't get a fake
    # COMPLETED. (Reaching here also proves no "final" state was seen —
    # final returns at the loop above.)
    raise _EngineRunFailed(
        "engine stream ended without a terminal frame",
        detail={
            "errorCode": "STREAM_TERMINATED_WITHOUT_FINAL",
            "errorMessage": (
                "The engine interaction stream ended before a terminal "
                "result was produced; the run did not complete."
            ),
        },
    )


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


__all__ = ["execute_and_stream"]
