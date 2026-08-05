"""_ChatPortMixin — chat_stream, chat_abort, and chat_inject port methods.

Also contains the module-level helpers _summarize_attachments and
_derive_event_name (relocated from engines/openclaw/chat.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from engine.community.kernel.frames import EventFrame
from engine.community.openclaw.config import get_config

log = logging.getLogger("openclaw-port")

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_INJECT_ASSISTANT_ONLY_FIELDS = ("api", "provider", "model", "stopReason", "usage")


# ── chat helpers (relocated from engines/openclaw/chat.py) ───────────────────


def _summarize_attachments(attachments: Any) -> dict[str, Any]:
    """Produce a log-safe summary of an attachments value.

    Relocated intact from `engines/openclaw/chat.py:_summarize_attachments`.
    """
    if attachments is None:
        return {"present": False, "count": 0}
    if not isinstance(attachments, list):
        return {"present": True, "valid": False, "type": type(attachments).__name__}

    items: list[dict[str, Any]] = []
    for index, item in enumerate(attachments):
        if not isinstance(item, dict):
            items.append({"index": index, "valid": False, "type": type(item).__name__})
            continue
        content = item.get("content")
        source = item.get("source")
        source_data = source.get("data") if isinstance(source, dict) else None
        items.append(
            {
                "index": index,
                "valid": True,
                "type": item.get("type"),
                "mimeType": item.get("mimeType") or item.get("media_type"),
                "fileName": item.get("fileName") or item.get("filename"),
                "contentLength": len(content) if isinstance(content, str) else None,
                "sourceType": source.get("type") if isinstance(source, dict) else None,
                "sourceMediaType": source.get("media_type") if isinstance(source, dict) else None,
                "sourceDataLength": len(source_data) if isinstance(source_data, str) else None,
            }
        )
    return {"present": True, "valid": True, "count": len(attachments), "items": items}


def _derive_event_name(event_data: dict[str, Any]) -> str:
    """Pick the EventFrame `event` name for a raw gateway event dict.

    Relocated intact from `engines/openclaw/chat.py:_derive_event_name`:
      - pops `_source_event` / `event` hint if present,
      - maps `state=approval_requested|approval_resolved` to the matching
        `approval.*` names,
      - defaults to `"agent"` for everything else.
    """
    explicit: Any = event_data.pop("_source_event", None) or event_data.pop("event", None)
    if explicit:
        return explicit
    state = event_data.get("state", "")
    if state == "approval_requested":
        return "approval.requested"
    if state == "approval_resolved":
        return "approval.resolved"
    return "agent"


class _ChatPortMixin:
    """Domain mixin: chat_stream and chat_abort (pooled, per-token)."""

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        idempotency_key: str | None = None,
        attachments: list[Any] | None = None,
        token: str | None = None,
    ) -> AsyncGenerator[EventFrame, None]:
        """Stream chat events from the OpenClaw gateway as EventFrames.

        Relocates the gateway loop from `engines/openclaw/chat.py` intact.
        `_derive_event_name` + `_summarize_attachments` are module-level helpers
        here. Transport errors (ConnectionError / Exception) propagate — the
        adapter is responsible for catching and converting them to error frames.
        The IntentEvalObserver is NOT constructed here; it lives in the adapter.

        Note: this is an async generator; the return type is
        `AsyncGenerator[EventFrame, None]` (a subtype of `AsyncIterator`).
        """
        if attachments is not None:
            log.info(
                "[chat_stream][attachments][port_forward] sessionKey=%s summary=%s",
                session_key,
                _summarize_attachments(attachments),
            )

        client = await self._pooled_client(token)
        async for event_data in client.chat_stream(
            session_key=session_key,
            message=message,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
            attachments=attachments,
        ):
            event_data.setdefault("sessionKey", session_key)
            state = event_data.get("state", "")
            event_name = _derive_event_name(event_data)
            frame = EventFrame(event=event_name, payload=event_data)
            yield frame

            if state in ("final", "error", "aborted"):
                run_id = event_data.get("runId", "")
                if isinstance(run_id, str) and run_id.startswith("inject-"):
                    continue
                log.info("[chat_stream] stream ended: state=%s", state)
                break

    async def chat_abort(
        self,
        session_key: str,
        run_id: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an in-flight chat run via the OpenClaw gateway.

        Returns the raw `{success, error, payload}` dict. On connection/RPC
        exceptions returns a synthetic failure dict so the adapter always
        receives a uniform dict.
        """
        try:
            client = await self._pooled_client(token)
        except Exception as e:
            log.exception("[chat_abort] connect failed: %s", e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}

        try:
            raw = await client.chat_abort(
                session_key=session_key,
                run_id=run_id,
            )
            return raw
        except Exception as e:
            log.exception("[chat_abort] RPC failed: %s", e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Inject a message via the OpenClaw gateway and return transcript ids.

        OpenClaw rejects ``chat.inject`` when the target session is missing.
        Preserve the transport-layer stopgap by creating the session via
        ``sessions.patch`` and retrying the inject once.
        """
        try:
            client = await self._pooled_client(token)
        except Exception as e:
            log.exception("[chat_inject] connect failed: %s", e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}

        params: dict[str, Any] = {"sessionKey": session_key, "message": message}
        if label:
            params["label"] = label

        try:
            response = await client.send_request("chat.inject", params, timeout=30.0)
            if not response.ok and _is_session_not_found_response(response):
                log.info(
                    "[chat_inject] session missing, creating via sessions.patch: sessionKey=%s",
                    session_key,
                )
                patch_response = await client.send_request(
                    "sessions.patch",
                    {"key": session_key},
                    timeout=30.0,
                )
                if not patch_response.ok:
                    return _failure_from_response(patch_response, "sessions.patch failed")
                response = await client.send_request("chat.inject", params, timeout=30.0)

            if not response.ok:
                return _failure_from_response(response, "chat.inject failed")

            payload = dict(response.payload) if isinstance(response.payload, dict) else {}
            describe_response = await client.send_request(
                "sessions.describe",
                {"key": session_key},
                timeout=30.0,
            )
            if describe_response.ok and isinstance(describe_response.payload, dict):
                session = describe_response.payload.get("session")
                if isinstance(session, dict) and isinstance(session.get("sessionId"), str):
                    payload["sessionId"] = session["sessionId"]
                    message_id = payload.get("messageId")
                    if isinstance(message_id, str):
                        transcript_path = _resolve_inject_transcript_path(session["sessionId"])
                        if transcript_path is not None:
                            _rewrite_injected_transcript_message(
                                transcript_path=transcript_path,
                                message_id=message_id,
                            )
                    else:
                        log.warning(
                            "[chat_inject] transcript rewrite skipped: messageId missing"
                        )
            else:
                log.warning(
                    "[chat_inject] sessions.describe failed after inject: sessionKey=%s error=%s",
                    session_key,
                    describe_response.error.to_dict() if describe_response.error else None,
                )

            return {"success": True, "payload": payload}
        except Exception as e:
            log.exception("[chat_inject] RPC failed: %s", e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}


def _failure_from_response(response: Any, fallback_message: str) -> dict[str, Any]:
    error = response.error.to_dict() if response.error else None
    return {
        "success": False,
        "error": error or {"code": "UNKNOWN", "message": fallback_message},
    }


def _is_session_not_found_response(response: Any) -> bool:
    if response.ok or response.error is None:
        return False
    message = response.error.message or ""
    return response.error.code == "INVALID_REQUEST" and "session not found" in message.lower()


def _resolve_inject_transcript_path(session_id: str) -> Path | None:
    # COSEC: validate the gateway-provided session id before deriving a local
    # filesystem path; OpenClaw 2026.5.12 sessions.describe does not expose sessionFile.
    if not _SAFE_SESSION_ID_RE.fullmatch(session_id):
        log.warning("[chat_inject] transcript rewrite skipped: unsafe sessionId")
        return None

    base_dir = Path(get_config().session_transcript_dir).expanduser().resolve()
    transcript_path = (base_dir / f"{session_id}.jsonl").resolve()
    try:
        transcript_path.relative_to(base_dir)
    except ValueError:
        log.warning("[chat_inject] transcript rewrite skipped: path escaped transcript dir")
        return None
    return transcript_path


def _rewrite_injected_transcript_message(transcript_path: Path, message_id: str) -> bool:
    try:
        raw = transcript_path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("[chat_inject] transcript rewrite skipped: read failed: %s", e)
        return False

    had_trailing_newline = raw.endswith("\n")
    lines = raw.splitlines()
    matched = False
    rewritten: list[str] = []

    for line in lines:
        if not line.strip():
            rewritten.append(line)
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            rewritten.append(line)
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if (
            isinstance(entry, dict)
            and entry.get("type") == "message"
            and entry.get("id") == message_id
            and isinstance(message, dict)
            and message.get("role") == "assistant"
        ):
            message["role"] = "user"
            for field in _INJECT_ASSISTANT_ONLY_FIELDS:
                message.pop(field, None)
            matched = True
            rewritten.append(json.dumps(entry, separators=(",", ":"), ensure_ascii=False))
        else:
            rewritten.append(line)

    if not matched:
        log.warning("[chat_inject] transcript rewrite skipped: messageId not found")
        return False

    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{transcript_path.name}.",
            suffix=".tmp",
            dir=str(transcript_path.parent),
            text=True,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write("\n".join(rewritten))
            if had_trailing_newline:
                tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, transcript_path)
        return True
    except OSError as e:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        log.warning("[chat_inject] transcript rewrite skipped: write failed: %s", e)
        return False
