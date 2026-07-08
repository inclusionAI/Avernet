"""_ChatPortMixin — chat_stream and chat_abort port methods.

Also contains the module-level helpers _summarize_attachments and
_derive_event_name (relocated from engines/openclaw/chat.py).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from engine.community.kernel.frames import EventFrame

log = logging.getLogger("openclaw-port")


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
