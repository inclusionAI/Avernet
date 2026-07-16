"""ClaudeCode session ACL adapter.

Implements the core `SessionService` by delegating to an injected
`ClaudeCodeSessionPort`. The adapter:
  - extracts ``auth.token`` and passes it as the routing key (pooled);
  - owns ALL dict→DTO construction (Session / Message / SessionResetResult)
    relocated from ``engines/claude_code/session.py``;
  - serialises request DTOs → (key, label, model, cwd) port arguments;
  - applies request-param-driven filtering / pagination (list path);
  - handles the reset in-band error dict → `SessionResetResult` (no raise).

Notable translation differences vs OpenClaw's session adapter
--------------------------------------------------------------
* Session-key parsing recovers ``(user_id, agent_id)`` from the relay's
  ``agent:<a>:session:<s>:user:<u>`` / legacy ``user:<u>:session:<s>:agent:<a>``
  forms; OpenClaw keys are ``session:<uuid>:user:<uid>`` and need a different
  label-suffix heuristic.
* The claude_code port exposes ``session_get_history`` (chat.history) and
  ``session_clear`` separately; OpenClaw's port exposes ``chat_history``.
* ``update`` reuses ``session_create`` (both call ``sessions.patch``) since
  the port has no dedicated update method, then re-lists to return the
  normalised Session — matching the corp impl.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
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
from engine.community.plugin_api.claude_code.session import ClaudeCodeSessionPort

log = logging.getLogger("claude-code-session-adapter")


# ── raw-dict → core-DTO builders (relocated from engines/claude_code/session.py) ──


def _parse_session_key(key: str) -> tuple[str | None, str | None]:
    """Extract (user_id, agent_id) from a canonical claude_code session key.

    Recognises two long forms:
      * new  ``agent:<a>:session:<s>:user:<u>``
      * legacy ``user:<u>:session:<s>:agent:<a>``

    Returns ``(None, None)`` for the legacy short form ``session:<uuid>`` and
    any other unparseable shape.
    """
    if key.startswith("agent:") and ":session:" in key and ":user:" in key:
        try:
            _, rest = key.split("agent:", 1)
            agent_part, rest2 = rest.split(":session:", 1)
            _, user_part = rest2.split(":user:", 1)
        except ValueError:
            return None, None
        if not user_part or not agent_part:
            return None, None
        return user_part, agent_part

    if key.startswith("user:") and ":session:" in key and ":agent:" in key:
        try:
            _, rest = key.split("user:", 1)
            user_part, rest2 = rest.split(":session:", 1)
            _, agent_part = rest2.split(":agent:", 1)
        except ValueError:
            return None, None
        if not user_part or not agent_part:
            return None, None
        return user_part, agent_part

    return None, None


def _parse_relay_timestamp(raw: Any) -> datetime | None:
    """Parse relay timestamps from millis-int, millis-string, or ISO string."""
    if raw is None:
        return None
    try:
        if isinstance(raw, int):
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
        value = str(raw).strip()
        if not value:
            return None
        numeric = value[:-1] if value.endswith("Z") else value
        if numeric.isdigit():
            return datetime.fromtimestamp(int(numeric) / 1000, tz=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _parse_message_count(raw: Any) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _relay_session_to_session(data: dict[str, Any], user_id: str = "default") -> Session:
    """Build a `Session` from a raw relay ``sessions.list`` entry dict.

    Relocated from ``ClaudeCodeSessionService._relay_session_to_session``.
    """
    key = data.get("key", "")
    parsed_user, parsed_agent = _parse_session_key(key)

    now = datetime.now(timezone.utc)
    updated_at = _parse_relay_timestamp(data.get("updatedAt")) or now
    created_at = _parse_relay_timestamp(data.get("createdAt")) or updated_at
    last_message_at = _parse_relay_timestamp(data.get("lastMessageAt"))
    message_created_at = last_message_at or updated_at
    message_count = _parse_message_count(data.get("messageCount"))

    preview = (data.get("preview") or "").strip()
    last_message: Message | None = None
    if preview or message_count > 0:
        source = "preview" if preview else "session-summary"
        last_message = Message(
            id=f"{key}:preview",
            session_id=key,
            role="assistant",
            content=preview,
            created_at=message_created_at,
            metadata={"source": source},
        )

    return Session(
        id=key,
        key=key,
        agent_id=parsed_agent,
        user_id=parsed_user or user_id,
        title=data.get("label") or key,
        status="active",
        created_at=created_at,
        updated_at=updated_at,
        last_message_at=message_created_at if last_message else None,
        model=data.get("model"),
        cwd=data.get("cwd"),
        permission_mode=data.get("permissionMode"),
        message_count=message_count,
        total_input_tokens=data.get("inputTokens", 0) or 0,
        total_output_tokens=data.get("outputTokens", 0) or 0,
        last_message=last_message,
    )


def _relay_message_to_message(data: dict[str, Any], session_id: str, index: int = 0) -> Message:
    """Build a `Message` from a raw relay ``chat.history`` entry dict.

    Relocated from ``ClaudeCodeSessionService._relay_message_to_message``.
    Mirrors the Moltis/OpenClaw message schema incl. ``<tool>{...}</tool>``
    rendering for tool_use / tool_result roles.
    """
    role = data.get("role", "user")

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
        tool_name = (
            metadata.get("toolName")
            or data.get("toolName")
            or data.get("tool_name")
            or "unknown"
        )
        tool_call_id = (
            metadata.get("toolCallId")
            or data.get("toolCallId")
            or data.get("tool_call_id")
        )
        is_error = metadata.get("isError", data.get("isError", False))
        success = not is_error
        result_content = metadata.get("output") or data.get("result") or content
        arguments = metadata.get("input") or data.get("arguments") or data.get("input")
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
        tool_name = (
            metadata.get("toolName")
            or data.get("toolName")
            or data.get("tool_name")
            or "unknown"
        )
        tool_call_id = (
            metadata.get("toolCallId")
            or data.get("toolCallId")
            or data.get("tool_call_id")
        )
        arguments = metadata.get("input") or data.get("arguments") or data.get("input")
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
                created_at = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            else:
                created_at = datetime.fromisoformat(
                    str(timestamp).replace("Z", "+00:00")
                )
        except (ValueError, TypeError):
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    return Message(
        id=msg_id,
        session_id=session_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        created_at=created_at,
        metadata=metadata,
    )


# ── Adapter ───────────────────────────────────────────────────────────────────


class ClaudeCodeSessionAdapter(SessionService):
    """`SessionService` over the claude_code native session port."""

    def __init__(self, port: ClaudeCodeSessionPort) -> None:
        self._port = port

    async def list(
        self,
        request: SessionListRequest,
        auth: AuthContext | None = None,
    ) -> list[Session]:
        """List sessions matching the request filter."""
        token = auth.token if auth is not None else None
        has_session_key = bool(request.session_key and request.session_key.strip())
        log.info(
            "[list] user_id=%s agent_id=%s has_session_key=%s",
            request.user_id,
            request.agent_id,
            has_session_key,
        )

        raw_sessions = await self._port.sessions_list(
            token=token,
            offset=request.offset,
            limit=request.limit,
            agent_id=request.agent_id,
            session_key=request.session_key,
        )

        sessions: list[Session] = []
        for raw in raw_sessions:
            try:
                key = raw.get("key", "")
                # Filter out bcs_grp_ sessions (aicoding BCS group sessions).
                if "bcs_grp_" in key:
                    continue
                session = _relay_session_to_session(
                    raw, user_id=request.user_id or "default"
                )
                if request.agent_id and session.agent_id != request.agent_id:
                    continue
                sessions.append(session)
            except Exception as e:
                log.warning("[list] conversion failed: %s raw=%s", e, raw)

        return sessions

    async def create(
        self,
        request: SessionCreateRequest,
        auth: AuthContext | None = None,
    ) -> Session:
        """Pre-allocate a sessionKey via ``sessions.patch`` and return it."""
        token = auth.token if auth is not None else None
        log.info("[create] title=%s model=%s user_id=%s", request.title, request.model, request.user_id)

        user_id = request.user_id or "default"
        session_uuid = request.uuid or str(uuid.uuid4())
        if request.agent_id and request.user_id:
            session_key = f"agent:{request.agent_id}:session:{session_uuid}:user:{user_id}"
        else:
            session_key = f"session:{session_uuid}"

        label = request.title or None

        await self._port.session_create(
            key=session_key,
            label=label,
            model=request.model,
            cwd=request.cwd,
            token=token,
        )

        now = datetime.now(timezone.utc)
        return Session(
            id=session_key,
            key=session_key,
            agent_id=request.agent_id,
            user_id=request.user_id,
            title=request.title or session_key,
            status="active",
            created_at=now,
            updated_at=now,
            last_message_at=None,
            model=request.model,
            cwd=request.cwd,
            permission_mode="bypassPermissions",
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
        """Clear a session's history via ``sessions.reset``.

        The port returns an in-band ``{success, error, payload}`` dict; we
        surface failure as a RuntimeError to match the corp impl (which raised
        on a non-success reset).
        """
        token = auth.token if auth is not None else None
        log.info("[clear] session_id=%s", request.session_id)
        raw = await self._port.session_clear(key=request.session_id, token=token)
        if not raw.get("success"):
            err = raw.get("error") or {}
            raise RuntimeError(
                f"Failed to clear session: {err.get('message', 'Unknown error')}"
            )

    async def get_history(
        self,
        request: SessionHistoryRequest,
        auth: AuthContext | None = None,
    ) -> SessionHistoryResult:
        """Return the message history for a session.

        Fetches raw message dicts from the port and builds Message DTOs,
        applying the offset/limit slice (request-param-driven).
        """
        token = auth.token if auth is not None else None
        log.info("[get_history] session_id=%s limit=%s", request.session_id, request.limit)

        raw_messages = await self._port.session_get_history(
            key=request.session_id,
            limit=request.limit if request.limit is not None else 100,
            token=token,
        )

        messages: list[Message] = []
        for i, msg_data in enumerate(raw_messages):
            try:
                messages.append(_relay_message_to_message(msg_data, request.session_id, i))
            except Exception as e:
                log.warning("[get_history] conversion failed: %s", e)

        start = request.offset
        end = start + request.limit if request.limit else len(messages)
        return SessionHistoryResult(messages=messages[start:end], total=None)

    async def update(
        self,
        request: SessionUpdateRequest,
        auth: AuthContext | None = None,
    ) -> Session:
        """Update session metadata via ``sessions.patch`` then re-list.

        The claude_code port exposes no dedicated update method; both create
        and update hit ``sessions.patch`` (``session_create``). After patching
        we re-list to return the server-normalised Session, merging any
        request fields the relay did not echo back — matching the corp impl.
        """
        token = auth.token if auth is not None else None
        log.info(
            "[update] session_id=%s title=%s model=%s permission_mode=%s",
            request.session_id, request.title, request.model, request.permission_mode,
        )

        if (
            not request.title
            and not request.model
            and not request.cwd
            and not request.permission_mode
        ):
            # No-op patch: scan the list and return the existing session.
            all_sessions = await self.list(SessionListRequest(limit=100), auth=auth)
            for s in all_sessions:
                if s.id == request.session_id:
                    return s
            raise RuntimeError(f"Session not found: {request.session_id}")

        # Reuse session_create (sessions.patch) for the update.
        await self._port.session_create(
            key=request.session_id,
            label=request.title,
            model=request.model,
            cwd=request.cwd,
            token=token,
        )

        all_sessions = await self.list(SessionListRequest(limit=100), auth=auth)
        for s in all_sessions:
            if s.id == request.session_id:
                if request.model and s.model != request.model:
                    s.model = request.model
                if request.permission_mode and s.permission_mode != request.permission_mode:
                    s.permission_mode = request.permission_mode
                if request.title and s.title != request.title:
                    s.title = request.title
                if request.cwd and s.cwd != request.cwd:
                    s.cwd = request.cwd
                return s

        # sessions.list not yet refreshed: return best-effort construct.
        now = datetime.now(timezone.utc)
        return Session(
            id=request.session_id,
            key=request.session_id,
            agent_id=request.agent_id,
            user_id=request.user_id or "default",
            title=request.title or request.session_id,
            status="active",
            created_at=now,
            updated_at=now,
            model=request.model,
            cwd=request.cwd,
            permission_mode=request.permission_mode,
        )

    async def reset(
        self,
        request: SessionResetRequest,
        auth: AuthContext | None = None,
    ) -> SessionResetResult:
        """Reset a session to a clean state via ``sessions.reset`` (no raise)."""
        token = auth.token if auth is not None else None
        log.info("[reset] session_key=%s", request.session_key)

        raw = await self._port.session_reset(key=request.session_key, token=token)

        if not raw.get("success"):
            err = raw.get("error") or {}
            return SessionResetResult(
                ok=False,
                error_code=err.get("code", "UNKNOWN"),
                error_message=err.get("message", "Unknown error"),
            )

        return SessionResetResult(ok=True, payload=raw.get("payload") or {})


__all__ = ["ClaudeCodeSessionAdapter"]
