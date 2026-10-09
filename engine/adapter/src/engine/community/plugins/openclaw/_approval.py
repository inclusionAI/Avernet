"""_ApprovalPortMixin — approvals_get and approvals_set port methods."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("openclaw-port")


class _ApprovalPortMixin:
    """Domain mixin: approval mode get/set (pooled, per-token)."""

    async def approvals_get(
        self,
        session_key: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `exec.approvals.get`; return `{"ok": bool, "payload": dict}`.

        Relocated from `engines/openclaw/approval.py:get_mode` up to the raw
        ResponseFrame payload extraction; the DTO build + raise-on-!ok moved
        to `core/adapters/openclaw/approval.py`.
        """
        client = await self._pooled_client(token)
        params = {"sessionKey": session_key} if session_key else {}
        log.debug(f"[approvals_get] session_key={session_key}")
        response = await client.send_request(
            method="exec.approvals.get",
            params=params,
            timeout=10.0,
        )
        if not response.ok:
            error_msg = (
                response.error.message if response.error else "Unknown error"
            )
            return {"ok": False, "error": error_msg}
        payload = response.payload or {}
        if not isinstance(payload, dict):
            payload = {}
        return {"ok": True, "payload": payload}

    async def approvals_set(
        self,
        session_key: str,
        mode: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `exec.approvals.set`; return `{"ok": bool, "payload": dict}`.

        Relocated from `engines/openclaw/approval.py:set_mode` up to the raw
        ResponseFrame payload extraction; the DTO build + raise-on-!ok moved
        to `core/adapters/openclaw/approval.py`.
        """
        client = await self._pooled_client(token)
        log.debug(f"[approvals_set] session_key={session_key} mode={mode}")
        response = await client.send_request(
            method="exec.approvals.set",
            params={"sessionKey": session_key, "mode": mode},
            timeout=10.0,
        )
        if not response.ok:
            error_msg = (
                response.error.message if response.error else "Unknown error"
            )
            return {"ok": False, "error": error_msg}
        payload = response.payload or {}
        if not isinstance(payload, dict):
            payload = {}
        return {"ok": True, "payload": payload}
