"""OpenClaw chat ACL adapter.

Implements the core `ChatService` by delegating to an injected
`OpenClawChatPort`. The adapter:
  - validates `request.sessionId` (raises ValueError if missing);
  - extracts `idempotency_key` + `attachments` from `request.extraParams`;
  - constructs `IntentEvalObserver` around the port's EventFrame stream;
  - preserves the critical finalize invariant: `observer.finalize()` is called
    ONLY on normal stream termination (after the async-for loop), NEVER inside
    the except arms — matching legacy `engines/openclaw/chat.py:174-220`;
  - converts transport errors to error EventFrames;
  - builds `ChatAbortResult` from the raw abort dict.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from engine.community.core.adapters.openclaw.intent_eval_observer import IntentEvalObserver
from engine.community.core.chat.models import ChatAbortRequest, ChatAbortResult, ChatRequest
from engine.community.core.chat.protocol import ChatService
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import ErrorShape, EventFrame
from engine.community.plugin_api.openclaw.chat import OpenClawChatPort

log = logging.getLogger("openclaw-chat-adapter")


class OpenClawChatAdapter(ChatService):
    """`ChatService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawChatPort) -> None:
        self._port = port

    async def inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        auth: AuthContext | None = None,
    ) -> dict[str, Any]:
        """Inject a message and repair OpenClaw's transcript-only assistant role."""
        token = auth.token if auth is not None else None
        raw = await self._port.chat_inject(
            session_key=session_key,
            message=message,
            label=label,
            token=token,
        )

        if not raw.get("success"):
            return {
                "ok": False,
                "error": raw.get("error")
                or {"code": "UNKNOWN", "message": "chat.inject failed"},
            }

        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        return {"ok": True, "payload": payload}

    async def stream(
        self,
        request: ChatRequest,
        auth: AuthContext | None = None,
    ) -> AsyncIterator[EventFrame]:
        """Stream chat events as EventFrames.

        Validates sessionId, parses extraParams, constructs the observer, then
        drives the port's chat_stream. The finalize invariant is preserved
        exactly as in `engines/openclaw/chat.py:174-220`:
          - `observer.finalize()` sits after the async-for, inside the `try`
            block but BEFORE any `except` arm — so it fires only on clean
            stream completion, never on exception paths.
        """
        session_key = request.sessionId
        if not session_key:
            raise ValueError(
                "ChatRequest.sessionId is required (pre-composed OpenClaw session key)"
            )

        # COSEC: hash session and prompt values before logging; the rewritten
        # prompt can contain sensitive Bot absolute paths.
        session_key_hash = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]
        log.info(
            "[stream] session_key_hash=%s query_len=%s query_hash=%s",
            session_key_hash,
            len(request.query),
            hashlib.sha256(request.query.encode("utf-8")).hexdigest()[:16],
        )

        # Extract idempotency_key and attachments from extraParams (adapter-side).
        idempotency_key: str | None = None
        attachments: list[Any] | None = None
        if isinstance(request.extraParams, dict):
            raw_idempotency_key = request.extraParams.get("idempotencyKey")
            if isinstance(raw_idempotency_key, str) and raw_idempotency_key.strip():
                idempotency_key = raw_idempotency_key.strip()
            raw_attachments = request.extraParams.get("attachments")
            if isinstance(raw_attachments, list):
                attachments = raw_attachments
            elif raw_attachments is not None:
                log.warning(
                    "[attachments][adapter_extra_invalid] session_key_hash=%s type=%s",
                    session_key_hash,
                    type(raw_attachments).__name__,
                )

        token = auth.token if auth is not None else None

        # Intent-eval side-effects. Construction fires emit_dialog_event.
        # observe() fires per-frame emit_agent_response tasks.
        # finalize() fires emit_agent_complete on normal termination ONLY.
        observer = IntentEvalObserver(
            session_key=session_key,
            user_message=request.query,
            token=token,
        )

        try:
            async for frame in self._port.chat_stream(
                session_key=session_key,
                message=request.query,
                timeout_ms=request.aliveTime,
                idempotency_key=idempotency_key,
                attachments=attachments,
                token=token,
            ):
                observer.observe(frame)
                yield frame

            # Normal-completion finalize. Sits inside the try block, after the
            # loop, matching legacy chat.py:199. NEVER called on exception paths.
            observer.finalize()

        except ConnectionError as e:
            log.error("[stream] connection error: %s", e)
            yield EventFrame(
                event="error",
                payload={
                    "sessionKey": session_key,
                    "state": "error",
                    "errorMessage": str(e),
                },
            )
        except Exception as e:
            log.exception("[stream] error: %s: %s", type(e).__name__, e)
            yield EventFrame(
                event="error",
                payload={
                    "sessionKey": session_key,
                    "state": "error",
                    "errorMessage": str(e),
                },
            )

    async def abort(
        self,
        request: ChatAbortRequest,
        auth: AuthContext | None = None,
    ) -> ChatAbortResult:
        """Cancel an in-flight chat run via the port, then build ChatAbortResult.

        The port returns a raw `{success, error, payload}` dict — this method
        builds the `ChatAbortResult` from it, synthesizing the aborted EventFrame
        exactly as `engines/openclaw/chat.py:260-293`.
        """
        token = auth.token if auth is not None else None
        raw = await self._port.chat_abort(
            session_key=request.session_key,
            run_id=request.run_id or "",
            token=token,
        )

        if not raw.get("success"):
            error_dict = raw.get("error") or {}
            return ChatAbortResult(
                ok=False,
                error=ErrorShape(
                    code=error_dict.get("code", "UNKNOWN"),
                    message=error_dict.get("message", "Unknown error"),
                ),
            )

        payload = raw.get("payload") or {}
        resolved_run_id = payload.get("runId") or request.run_id
        aborted = bool(payload.get("aborted", False))

        emit_events: list[EventFrame] = []
        if aborted:
            emit_events.append(
                EventFrame(
                    event="chat",
                    payload={
                        "runId": resolved_run_id or "",
                        "sessionKey": request.session_key,
                        "state": "aborted",
                        "stopReason": "rpc",
                    },
                )
            )

        return ChatAbortResult(
            ok=True,
            aborted=aborted,
            run_id=resolved_run_id,
            emit_events=emit_events,
        )


__all__ = ["OpenClawChatAdapter"]
