"""OpenClaw session ACL adapter.

Implements the core `SessionService` by delegating to an injected
`OpenClawSessionPort`.  The adapter:
  - extracts `auth.token` and passes it as the routing key (pooled);
  - owns ALL dict→DTO construction (Session / Message / SessionResetResult)
    relocated from `engines/openclaw/session.py`;
  - serialises request DTOs → (key, label, model) port arguments (write path);
  - applies request-param-driven filtering / pagination (list path — these
    depend on `SessionListRequest` fields so they belong here, not in the impl);
  - handles the reset in-band error dict → `SessionResetResult` (no raise).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.session.models import (
    Message,
    Session,
    SessionClearRequest,
    SessionCreateRequest,
    SessionDeleteRequest,
    SessionHistoryRequest,
    SessionHistoryResult,
    SessionListRequest,
    SessionResetRequest,
    SessionResetResult,
    SessionUpdateRequest,
)
from engine.community.core.session.protocol import SessionService
from engine.community.plugin_api.openclaw.session import OpenClawSessionPort

log = logging.getLogger("openclaw-session-adapter")

# role 映射：OpenClaw 驼峰 -> Message 下划线 (relocated from legacy service)
_ROLE_MAP: dict[str, str] = {
    "toolResult": "tool_result",
    "toolUse": "tool_use",
    "ToolResult": "tool_result",
    "ToolUse": "tool_use",
}


def _session_label_suffix(value: object) -> str:
    """Return a stable label suffix derived from a session uuid/key.

    Best-effort and intentionally non-raising: label construction must never
    turn a create/update request into a failure just because the session id has
    an unexpected shape.  Expected OpenClaw keys look like
    ``session:<uuid>:user:<user_id>``; for other inputs we fall back to the
    string form, and finally to a generated uuid if even string conversion
    fails.
    """
    try:
        text = str(value or "").strip()
        if not text:
            return uuid.uuid4().hex

        parts = text.split(":")
        if len(parts) >= 4 and parts[0] == "session" and parts[2] == "user" and parts[1]:
            return parts[1]

        return text
    except Exception:
        return uuid.uuid4().hex


# ── raw-dict → core-DTO builders (relocated from engines/openclaw/session.py) ──


def _to_session(data: dict[str, Any], index: int = 0) -> Session:
    """Build a `Session` from a raw OpenClaw gateway session dict.

    Relocated from `OpenClawSessionService._to_session`.  Model normalisation is
    impl-side (stored as `_normalized_model` in the dict); this function reads
    that pre-computed value directly.
    """
    log.info("[_to_session] raw data: %s", data)
    key = data.get("key", "")
    now = datetime.now(UTC)
    # The impl already normalised the model string and stored it as _normalized_model.
    normalized_model = data.get("_normalized_model") or data.get("model")
    return Session(
        id=key,
        key=key,
        agent_id=None,
        user_id="default",
        title=data.get("label") or data.get("displayName") or key,
        status="active",
        created_at=now,
        updated_at=now,
        last_message_at=now if data.get("preview") else None,
        model=normalized_model,
        message_count=data.get("_message_count", 0),
        total_input_tokens=data.get("inputTokens", 0),
        total_output_tokens=data.get("outputTokens", 0),
    )


def _to_message(data: dict[str, Any], session_id: str, index: int = 0) -> Message:
    """Build a `Message` from a raw OpenClaw gateway message dict.

    Relocated from `OpenClawSessionService._to_message`.
    """
    raw_role = data.get("role", "user")
    role = _ROLE_MAP.get(raw_role, raw_role)

    content_raw = data.get("content", "")
    if isinstance(content_raw, list):
        text_parts: list[str] = []
        for item in content_raw:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif "text" in item:
                    text_parts.append(item["text"])
            elif isinstance(item, str):
                text_parts.append(item)
        content = "\n".join(text_parts)
    else:
        content = str(content_raw)

    metadata = data.get("metadata") or {}
    if role == "tool_result":
        tool_name = data.get("toolName", "unknown")
        tool_call_id = data.get("toolCallId")
        is_error = data.get("isError", False)
        success = not is_error
        result_content = content
        arguments = data.get("arguments") or data.get("input")

        metadata.update({
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "success": success,
            "result": result_content,
            "arguments": arguments,
        })
        tool_params = {"name": tool_name, "success": success, "running": False}
        content = f"\n\n<tool>{json.dumps(tool_params)}</tool>\n\n"
    elif role == "tool_use":
        tool_name = data.get("toolName", "unknown")
        tool_call_id = data.get("toolCallId")
        arguments = data.get("arguments") or data.get("input")
        metadata.update({
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
        })
        tool_params = {"name": tool_name, "success": False, "running": True}
        content = f"\n\n<tool>{json.dumps(tool_params)}</tool>\n\n"

    msg_id = data.get("id") or f"{session_id}_{index}"

    timestamp = data.get("timestamp") or data.get("created_at")
    if timestamp:
        try:
            if isinstance(timestamp, int):
                created_at = datetime.fromtimestamp(timestamp / 1000, tz=UTC)
            else:
                created_at = datetime.fromisoformat(
                    str(timestamp).replace("Z", "+00:00")
                )
        except (ValueError, TypeError):
            created_at = datetime.now(UTC)
    else:
        created_at = datetime.now(UTC)

    return Message(
        id=msg_id,
        session_id=session_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        created_at=created_at,
        metadata=metadata,
        history_meta=data.get("historyMeta"),
    )


# ── Adapter ───────────────────────────────────────────────────────────────────


class OpenClawSessionAdapter(SessionService):
    """`SessionService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawSessionPort) -> None:
        self._port = port

    async def list(
        self,
        request: SessionListRequest,
        auth: AuthContext | None = None,
    ) -> list[Session]:
        """List sessions matching the request filter.

        The port handles the complete legacy ordering: sessions.list RPC →
        bcs:group filter → agent_id filter → paginate → chat.history (page only)
        → Bot 初始化配置 filter → model normalisation.  The adapter receives
        the already-filtered, already-paginated page and only builds DTOs.
        """
        token = auth.token if auth is not None else None
        log.info("[list] user_id=%s, agent_id=%s", request.user_id, request.agent_id)

        raw_sessions = await self._port.sessions_list(
            token=token,
            offset=request.offset,
            limit=request.limit,
            agent_id=request.agent_id,
        )

        sessions: list[Session] = []
        for i, raw in enumerate(raw_sessions):
            try:
                session = _to_session(raw, i)
                # Populate last_message from the pre-fetched _messages raw list.
                messages_raw = raw.get("_messages", [])
                if messages_raw:
                    try:
                        session.last_message = _to_message(
                            messages_raw[-1], session.id, len(messages_raw) - 1
                        )
                    except Exception as e:
                        log.warning("[list] failed to build last_message: %s", e)
                sessions.append(session)
            except Exception as e:
                log.warning("[list] failed to convert session: %s", e)

        return sessions

    async def create(
        self,
        request: SessionCreateRequest,
        auth: AuthContext | None = None,
    ) -> Session:
        """Create a new session and return it."""
        token = auth.token if auth is not None else None
        log.info(
            "[create] title=%s, model=%s, user_id=%s",
            request.title, request.model, request.user_id,
        )

        user_id = request.user_id or "default"
        session_uuid = request.uuid or str(uuid.uuid4())
        session_key = f"session:{session_uuid}:user:{user_id}"

        label: str | None = None
        if request.title:
            label = f"{request.title}_{_session_label_suffix(session_uuid)}"

        patch_params = await self._port.session_create(
            key=session_key,
            label=label,
            model=request.model,
            token=token,
        )

        now = datetime.now(UTC)
        return Session(
            id=session_key,
            key=session_key,
            agent_id=request.agent_id,
            user_id=request.user_id,
            title=patch_params.get("label") or session_key,
            status="active",
            created_at=now,
            updated_at=now,
            last_message_at=None,
            model=request.model,
            message_count=0,
            total_input_tokens=0,
            total_output_tokens=0,
        )

    async def delete(
        self,
        request: SessionDeleteRequest,
        auth: AuthContext | None = None,
    ) -> bool:
        """Delete a session."""
        token = auth.token if auth is not None else None
        log.info("[delete] session_id=%s", request.session_id)
        return await self._port.session_delete(key=request.session_id, token=token)

    async def clear(
        self,
        request: SessionClearRequest,
        auth: AuthContext | None = None,
    ) -> None:
        """Clear a session's history."""
        token = auth.token if auth is not None else None
        log.info("[clear] session_id=%s", request.session_id)
        await self._port.session_clear(key=request.session_id, token=token)

    async def get_history(
        self,
        request: SessionHistoryRequest,
        auth: AuthContext | None = None,
    ) -> SessionHistoryResult:
        """Return the message history for a session.

        The port fetches the raw message dicts; this adapter builds Message DTOs
        and applies the offset/limit slice (request-param-driven).
        """
        token = auth.token if auth is not None else None
        log.info(
            "[get_history] session_id=%s, limit=%s",
            request.session_id, request.limit,
        )

        raw_messages = await self._port.chat_history(
            session_key=request.session_id,
            limit=request.limit,
            token=token,
        )

        messages: list[Message] = []
        for i, msg_data in enumerate(raw_messages):
            try:
                messages.append(_to_message(msg_data, request.session_id, i))
            except Exception as e:
                log.warning("[get_history] failed to convert message: %s", e)

        # Offset/limit slice (request-param-driven).
        start = request.offset
        end = start + request.limit if request.limit else len(messages)
        return SessionHistoryResult(messages=messages[start:end], total=None)

    async def update(
        self,
        request: SessionUpdateRequest,
        auth: AuthContext | None = None,
    ) -> Session:
        """Update session metadata and return the updated object.

        Delegates to `session_patch_then_get` which relocates the legacy
        patch → full-list scan body intact.  When there is nothing to patch
        (no title, no model) we fall back to scanning the list to return the
        existing session — matching legacy behaviour exactly.
        """
        token = auth.token if auth is not None else None
        log.info(
            "[update] session_id=%s, title=%s, model=%s",
            request.session_id, request.title, request.model,
        )

        if not request.title and not request.model:
            # No-op patch: scan the list and return the existing session.
            all_sessions = await self.list(
                SessionListRequest(limit=100), auth=auth
            )
            for s in all_sessions:
                if s.id == request.session_id:
                    return s
            raise RuntimeError(f"Session not found: {request.session_id}")

        label: str | None = None
        if request.title:
            label = f"{request.title}_{_session_label_suffix(request.session_id)}"

        raw = await self._port.session_patch_then_get(
            key=request.session_id,
            label=label,
            model=request.model,
            token=token,
        )
        return _to_session(raw)

    async def reset(
        self,
        request: SessionResetRequest,
        auth: AuthContext | None = None,
    ) -> SessionResetResult:
        """Reset a session to a clean state.

        Calls `session_reset` on the port which always returns an in-band dict.
        Converts to `SessionResetResult` without raising — matching legacy
        `OpenClawSessionService.reset` exactly.
        """
        token = auth.token if auth is not None else None
        log.info("[reset] session_key=%s", request.session_key)

        raw = await self._port.session_reset(
            session_key=request.session_key, token=token
        )

        if not raw.get("success"):
            error_dict = raw.get("error") or {}
            return SessionResetResult(
                ok=False,
                error_code=error_dict.get("code", "UNKNOWN"),
                error_message=error_dict.get("message", "Unknown error"),
            )

        return SessionResetResult(ok=True, payload=raw.get("payload") or {})


__all__ = ["OpenClawSessionAdapter"]
