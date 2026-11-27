"""Exec-only A2A approval ledger; native WS forwarding remains unchanged."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


def _session(value: str) -> str:
    return value.strip().lower().removeprefix("agent:main:")


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}


@dataclass
class PendingExec:
    session_key: str
    expires_at: float
    token: str | None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    attempted: bool = False


class ExecApprovalLedger:
    """Only native, session-bound, unexpired approval IDs may be resolved."""

    def __init__(self) -> None:
        self.pending: dict[str, PendingExec] = {}

    def normalize(self, session_key: str, payload: dict[str, Any],
                  token: str | None) -> dict[str, Any] | None:
        now = time.time() * 1000
        for key, record in list(self.pending.items()):
            if record.expires_at <= now:
                self.pending.pop(key, None)
        iid = payload.get("approvalId") or payload.get("id")
        if not isinstance(iid, str) or not iid:
            return None
        state = payload.get("state")
        request = payload.get("request")
        request = request if isinstance(request, dict) else {}
        # chat_stream adds top-level sessionKey/runId; neither proves provenance.
        native_session = request.get("sessionKey")
        if state == "approval_requested":
            if not isinstance(native_session, str) or _session(native_session) != _session(session_key):
                return None
            expires = payload.get("expiresAtMs")
            if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not now < expires < float("inf"):
                return None
            existing = self.pending.get(iid)
            if existing and existing.session_key != session_key:
                return None
            if existing is None:
                if len(self.pending) >= 1024:
                    return None
                self.pending[iid] = PendingExec(session_key, expires, token)
        elif state == "approval_resolved":
            existing = self.pending.get(iid)
            if existing is None or existing.session_key != session_key:
                return None
            self.pending.pop(iid, None)
        else:
            return None
        return {**payload, "id": iid, "approvalId": iid, "kind": "exec",
                "sessionKey": session_key}

    def clear_session(self, session_key: str, owned_ids: set[str]) -> None:
        for iid, record in list(self.pending.items()):
            if iid in owned_ids and record.session_key == session_key:
                self.pending.pop(iid, None)

    async def resolve(self, port, *, session_key: str, interaction_id: str,
                      decision: str, message: str | None = None) -> dict[str, Any]:
        if decision not in ("allow-once", "deny") or message:
            return _failure("INVALID_REQUEST", "Unsupported exec approval decision or message")
        record = self.pending.get(interaction_id)
        if record is None or record.session_key != session_key:
            return _failure("NOT_FOUND", "Approval is not pending in this session")
        async with record.lock:
            if self.pending.get(interaction_id) is not record or record.expires_at <= time.time() * 1000:
                return _failure("NOT_FOUND", "Approval has expired or was resolved")
            if record.attempted:
                return _failure("INTERNAL_ERROR", "Previous approval submission outcome is unknown")
            # No automatic duplicate dispatch after a timeout or lost response.
            record.attempted = True
            try:
                result = await port.exec_approval_resolve(
                    approval_id=interaction_id, decision=decision, token=record.token)
            except asyncio.CancelledError:
                raise
            except Exception:
                return _failure("INTERNAL_ERROR", "Approval submission outcome is unknown")
            if result.get("success") and self.pending.get(interaction_id) is record:
                self.pending.pop(interaction_id, None)
            return result
