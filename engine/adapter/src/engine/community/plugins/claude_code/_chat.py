"""_ChatPortMixin — chat streaming, abort, inject, and HITL resolution.

Relocates the relay-loop + frame translation from the corp
``engines/claude_code/chat.py`` (read-only reference) onto the community
transport. The loop drives the ``ClaudeCodeRelayClient.chat_stream`` async
generator (which speaks the relay v3 protocol); this mixin only derives the
``EventFrame.event`` name, injects ``sessionKey``, and enforces the
terminal-state break + inject-runId skip semantics.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from engine.community.kernel.frames import EventFrame

log = logging.getLogger("claude-code-community-port")

# Top-level event channels — forwarded by name rather than flattened to
# ``"agent"`` (the upstream WS server / business layer dispatches on these,
# e.g. to surface an approval UI or an AskUserQuestion interaction).
_TOPLEVEL_EVENTS: frozenset[str] = frozenset(
    {
        "chat",
        "tick",
        "exec.approval.requested",
        "exec.approval.resolved",
        "interaction.requested",
        "interaction.resolved",
        "mode_transition.resolved",
    }
)


def _derive_event_name(event_data: dict[str, Any]) -> str:
    """Pick the ``EventFrame.event`` name for a raw relay payload.

    Precedence (first match wins):

    1. ``_source_event`` / ``event`` hint injected by the relay client — restores
       the top-level event name after the shared stream queue.
    2. Legacy ``state`` field (``error`` / ``aborted``) → own event name so
       fatal terminations are distinguishable from a successful ``final``.
    3. Default ``"agent"`` for everything else.

    Side effect: the hint keys are *popped* off the payload so they don't leak
    to the frontend as mystery fields.
    """
    explicit = event_data.pop("_source_event", None) or event_data.pop("event", None)
    if explicit in _TOPLEVEL_EVENTS:
        return explicit
    if explicit:
        return explicit
    state = event_data.get("state", "")
    if state == "error":
        return "error"
    if state == "aborted":
        return "aborted"
    return "agent"


class _ChatPortMixin:
    """Domain mixin: chat_stream / abort / inject + HITL resolves."""

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        cwd: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        attachments: list[Any] | None = None,
        token: str | None = None,
    ) -> AsyncGenerator[EventFrame, None]:
        """Stream chat events from the relay as EventFrames.

        Transport errors (ConnectionError / Exception) propagate — the adapter
        catches and converts them to error frames.
        """
        client = await self._relay()
        async for event_data in client.chat_stream(
            session_key=session_key,
            message=message,
            timeout_ms=timeout_ms,
            cwd=cwd,
            model=model,
            permission_mode=permission_mode,
            attachments=attachments,
        ):
            event_data.setdefault("sessionKey", session_key)
            state = event_data.get("state", "")
            event_name = _derive_event_name(event_data)
            yield EventFrame(event=event_name, payload=event_data)

            if state in ("final", "error", "aborted"):
                run_id = event_data.get("runId", "")
                if isinstance(run_id, str) and run_id.startswith("inject-"):
                    # An inject-generated final must NOT terminate the stream
                    # (matches legacy chat.py inject-skip).
                    continue
                log.info("[chat_stream] stream ended: state=%s", state)
                break

    async def chat_abort(
        self,
        session_key: str,
        run_id: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an in-flight chat run via ``chat.abort``."""
        try:
            client = await self._relay()
        except Exception as e:  # noqa: BLE001
            log.exception("[chat_abort] connect failed: %s", e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}

        params: dict[str, Any] = {"sessionKey": session_key}
        if run_id is not None:
            params["runId"] = run_id
        try:
            resp = await client.send_request("chat.abort", params, timeout=10.0)
        except Exception as e:  # noqa: BLE001
            log.exception("[chat_abort] RPC failed: %s", e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        return _response_to_dict(resp)

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Inject a message into session history via ``chat.inject``."""
        try:
            client = await self._relay()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}

        params: dict[str, Any] = {"sessionKey": session_key, "message": message}
        if label is not None:
            params["label"] = label
        try:
            resp = await client.send_request("chat.inject", params, timeout=15.0)
        except Exception as e:  # noqa: BLE001
            log.exception("[chat_inject] RPC failed: %s", e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        return _response_to_dict(resp)

    async def _resolve_with_events(
        self,
        method: str,
        params: dict[str, Any],
        event_name: str,
    ) -> dict[str, Any]:
        """Shared resolve path: send + collect the ``*.resolved`` followup event."""
        try:
            client = await self._relay()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        try:
            resp, events = await client.send_request_with_events(
                method, params, [event_name], session_key=None,
                response_timeout=30.0,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("[%s] RPC failed: %s", method, e)
            return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        out = _response_to_dict(resp)
        out["followup_events"] = [(e.event, dict(e.payload or {})) for e in events]
        return out

    async def resolve_exec_approval(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        message: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending exec approval via ``interaction.resolve``."""
        params: dict[str, Any] = {
            "interactionId": run_id,
            "decision": decision,
        }
        if message is not None:
            params["message"] = message
        return await self._resolve_with_events(
            "interaction.resolve", params, "interaction.resolved")

    async def resolve_interaction(
        self,
        session_key: str,
        run_id: str,
        response: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending AskUserQuestion via ``interaction.resolve`` (submit)."""
        params: dict[str, Any] = {
            "interactionId": run_id,
            "action": "submit",
        }
        if response is not None:
            params["message"] = response
        return await self._resolve_with_events(
            "interaction.resolve", params, "interaction.resolved")

    async def resolve_mode_transition(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending ExitPlanMode via ``mode_transition.resolve``."""
        params = {"transitionId": run_id, "decision": decision}
        return await self._resolve_with_events(
            "mode_transition.resolve", params, "mode_transition.resolved")


def _response_to_dict(resp: Any) -> dict[str, Any]:
    """Normalize a ``ResponseFrame`` into the ``{success, payload|error}`` shape."""
    if resp.ok:
        return {"success": True, "payload": resp.payload or {}}
    err = resp.error
    return {
        "success": False,
        "error": {
            "code": err.code if err else "UNKNOWN",
            "message": err.message if err else "Unknown error",
        },
    }
