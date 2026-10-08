"""ClaudeCode chat ACL adapter.

Implements the core `ChatService` by delegating to an injected
`ClaudeCodeChatPort`. The adapter:
  - derives the relay sessionKey from `ChatRequest` (pre-built keys pass
    through; otherwise the canonical ``agent:<a>:session:<s>:user:<u>`` form
    is built);
  - extracts ``cwd`` / ``model`` / ``permissionMode`` hints from
    ``ChatRequest.extraParams``;
  - drives the port's `chat_stream` async generator and yields the
    fully-formed `EventFrame`s it emits;
  - converts transport errors to error `EventFrame`s;
  - builds `ChatAbortResult` from the raw ``{success, error, payload}`` dict.

Divergence from OpenClaw's chat adapter
---------------------------------------
The claude_code port's `chat_stream` already returns `EventFrame`s (the
gateway frame-translation loop lives inside the port impl, not the adapter),
and the OpenClaw-specific `IntentEvalObserver` (Langfuse intent-eval
side-effects) is NOT wired here — claude_code's corp impl never constructed
one, so the OSS adapter likewise skips it. The finalize invariant from
OpenClaw therefore does not apply: there is no observer to finalize.

The adapter additionally exposes two claude_code-extra methods
(`inject`, `resolve_exec_approval`, `resolve_interaction`,
`resolve_mode_transition`) that are NOT part of the `ChatService` Protocol
but are surfaced by the engine aggregate for the WS server's HITL dispatch.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from engine.community.core.chat.models import ChatAbortRequest, ChatAbortResult, ChatRequest
from engine.community.core.chat.protocol import ChatService
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import ErrorShape, EventFrame
from engine.community.plugin_api.claude_code.chat import ClaudeCodeChatPort

log = logging.getLogger("claude-code-chat-adapter")


class ClaudeCodeChatAdapter(ChatService):
    """`ChatService` over the claude_code native chat port."""

    def __init__(self, port: ClaudeCodeChatPort) -> None:
        self._port = port

    # ── request helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_session_key(request: ChatRequest) -> str:
        """Derive the relay sessionKey from a ChatRequest.

        Preserves pre-built keys (``agent:`` / ``user:`` / ``session:`` prefixes)
        and otherwise builds the canonical
        ``agent:<a>:session:<s>:user:<u>`` form.
        """
        session_id = request.sessionId or str(uuid.uuid4())
        if (
            session_id.startswith("agent:")
            or session_id.startswith("user:")
            or session_id.startswith("session:")
        ):
            return session_id
        return f"agent:{request.agentId}:session:{session_id}:user:{request.userId}"

    @staticmethod
    def _extract_cwd(request: ChatRequest) -> str | None:
        """Pull the optional ``cwd`` hint from ``extraParams``.

        Accepts both ``cwd`` and ``cwd_path`` spellings. Empty → None.
        """
        extra = request.extraParams or {}
        value = extra.get("cwd") or extra.get("cwd_path")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_model(request: ChatRequest) -> str | None:
        """Pull the optional ``model`` selector from ``extraParams``."""
        extra = request.extraParams or {}
        value = extra.get("model")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_permission_mode(request: ChatRequest) -> str | None:
        """Pull ``permissionMode`` from ``extraParams`` (``permission_mode`` alias)."""
        extra = request.extraParams or {}
        value = extra.get("permissionMode") or extra.get("permission_mode")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    # ── ChatService.stream ───────────────────────────────────────────────────

    async def stream(
        self,
        request: ChatRequest,
        auth: AuthContext | None = None,
    ) -> AsyncIterator[EventFrame]:
        """Stream chat events as EventFrames.

        Drives the port's ``chat_stream`` and yields the EventFrames it emits.
        The port handles relay-frame → EventFrame translation internally, so
        the adapter is a thin driver. Transport errors are converted to error
        EventFrames.
        """
        session_key = self._build_session_key(request)
        cwd = self._extract_cwd(request)
        model = self._extract_model(request)
        permission_mode = self._extract_permission_mode(request)
        token = auth.token if auth is not None else None

        attachments: list[Any] | None = None
        if isinstance(request.extraParams, dict):
            raw_attachments = request.extraParams.get("attachments")
            if isinstance(raw_attachments, list):
                attachments = raw_attachments

        # COSEC: hash session and prompt values before logging; the rewritten
        # prompt can contain sensitive Bot absolute paths.
        log.info(
            "[stream] key_hash=%s cwd=%s model=%s permission_mode=%s has_token=%s query_len=%s query_hash=%s",
            hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16],
            cwd,
            model,
            permission_mode,
            token is not None,
            len(request.query),
            hashlib.sha256(request.query.encode("utf-8")).hexdigest()[:16],
        )

        try:
            async for frame in self._port.chat_stream(
                session_key=session_key,
                message=request.query,
                timeout_ms=request.aliveTime,
                cwd=cwd,
                model=model,
                permission_mode=permission_mode,
                attachments=attachments,
                token=token,
            ):
                yield frame
        except ConnectionError as e:
            log.error("[stream] connection error: %s", e)
            yield EventFrame(
                event="error",
                payload={
                    "sessionKey": session_key,
                    "state": "error",
                    "errorCode": "CONNECTION_ERROR",
                    "errorMessage": str(e),
                },
            )
        except Exception as e:
            log.exception("[stream] %s: %s", type(e).__name__, e)
            yield EventFrame(
                event="error",
                payload={
                    "sessionKey": session_key,
                    "state": "error",
                    "errorCode": "INTERNAL_ERROR",
                    "errorMessage": str(e),
                },
            )

    # ── ChatService.abort ────────────────────────────────────────────────────

    async def abort(
        self,
        request: ChatAbortRequest,
        auth: AuthContext | None = None,
    ) -> ChatAbortResult:
        """Cancel an in-flight chat run via the port and build ChatAbortResult."""
        token = auth.token if auth is not None else None
        try:
            raw = await self._port.chat_abort(
                session_key=request.session_key,
                run_id=request.run_id,
                token=token,
            )
        except Exception as e:
            log.exception("[abort] error: %s", e)
            return ChatAbortResult(
                ok=False,
                error=ErrorShape(code="INTERNAL_ERROR", message=str(e)),
            )

        if not raw.get("success"):
            err = raw.get("error") or {}
            return ChatAbortResult(
                ok=False,
                error=ErrorShape(
                    code=err.get("code", "UNKNOWN"),
                    message=err.get("message", "Unknown error"),
                ),
            )

        payload = raw.get("payload") or {}
        aborted = bool(payload.get("aborted", False))
        run_id = payload.get("runId") or request.run_id

        emit_events: list[EventFrame] = []
        if aborted:
            emit_events.append(EventFrame(
                event="chat",
                payload={
                    "runId": run_id or "",
                    "sessionKey": request.session_key,
                    "state": "aborted",
                    "stopReason": "rpc",
                },
            ))

        log.info(
            "[abort] session=%s run_id=%s aborted=%s",
            request.session_key, run_id, aborted,
        )
        return ChatAbortResult(
            ok=True,
            aborted=aborted,
            run_id=run_id,
            emit_events=emit_events,
        )

    # ── claude_code-extra methods (not on ChatService Protocol) ──────────────

    async def inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        auth: AuthContext | None = None,
    ) -> dict[str, Any]:
        """Inject a chat message into the session transcript without running the agent.

        Translates the port's ``{success, error, payload}`` dict into the
        ``{ok, payload|error}`` shape the WS server consumes.
        """
        if not session_key:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "session_key required"}}
        if not message:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "message required"}}

        token = auth.token if auth is not None else None
        try:
            raw = await self._port.chat_inject(
                session_key=session_key,
                message=message,
                label=label,
                token=token,
            )
        except Exception as e:
            log.exception("[inject] error session=%s: %s", session_key, e)
            return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}

        if not raw.get("success"):
            err = raw.get("error") or {}
            return {
                "ok": False,
                "error": {
                    "code": err.get("code", "UNKNOWN"),
                    "message": err.get("message", "Unknown error"),
                },
            }
        return {"ok": True, "payload": raw.get("payload") or {}}

    async def resolve_exec_approval(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        message: str | None = None,
        auth: AuthContext | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending exec approval via the port."""
        token = auth.token if auth is not None else None
        return await self._port.resolve_exec_approval(
            session_key=session_key,
            run_id=run_id,
            decision=decision,
            message=message,
            token=token,
        )

    async def resolve_interaction(
        self,
        session_key: str,
        run_id: str,
        response: str | None = None,
        auth: AuthContext | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending AskUserQuestion interaction via the port."""
        token = auth.token if auth is not None else None
        return await self._port.resolve_interaction(
            session_key=session_key,
            run_id=run_id,
            response=response,
            token=token,
        )

    async def resolve_mode_transition(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        auth: AuthContext | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending ExitPlanMode transition via the port."""
        token = auth.token if auth is not None else None
        return await self._port.resolve_mode_transition(
            session_key=session_key,
            run_id=run_id,
            decision=decision,
            token=token,
        )


__all__ = ["ClaudeCodeChatAdapter"]
