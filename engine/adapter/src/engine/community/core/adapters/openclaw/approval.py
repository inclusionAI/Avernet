"""OpenClaw approval ACL adapter.

Implements the core `ApprovalService` by delegating to an injected
`OpenClawApprovalPort`.  The adapter:
  - extracts `auth.token` and passes it as the routing key;
  - builds `ApprovalModeGetResult` / `ApprovalModeSetResult` from the raw dict
    the port returns;
  - raises `RuntimeError` on !ok exactly as the legacy `OpenClawApprovalService`
    did (preserving the error message format "exec.approvals.<x> failed: <msg>").
"""
from __future__ import annotations

import logging

from engine.community.core.approval.models import (
    ApprovalModeGetRequest,
    ApprovalModeGetResult,
    ApprovalModeSetRequest,
    ApprovalModeSetResult,
)
from engine.community.core.approval.protocol import ApprovalService
from engine.community.core.engine.context import AuthContext
from engine.community.plugin_api.openclaw.approval import OpenClawApprovalPort

log = logging.getLogger("openclaw-approval-adapter")


class OpenClawApprovalAdapter(ApprovalService):
    """`ApprovalService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawApprovalPort) -> None:
        self._port = port

    async def get_mode(
        self,
        request: ApprovalModeGetRequest,
        auth: AuthContext | None = None,
    ) -> ApprovalModeGetResult:
        """Read the session's approval mode via `exec.approvals.get`."""
        token = auth.token if auth is not None else None
        log.debug(f"[get_mode] session_key={request.session_key}")
        result = await self._port.approvals_get(
            session_key=request.session_key,
            token=token,
        )
        if not result.get("ok"):
            error_msg = result.get("error", "Unknown error")
            raise RuntimeError(f"exec.approvals.get failed: {error_msg}")
        payload = result.get("payload") or {}
        mode = payload.get("mode") if isinstance(payload, dict) else None
        return ApprovalModeGetResult(
            mode=mode,
            payload=payload if isinstance(payload, dict) else {},
        )

    async def set_mode(
        self,
        request: ApprovalModeSetRequest,
        auth: AuthContext | None = None,
    ) -> ApprovalModeSetResult:
        """Set the session's approval mode via `exec.approvals.set`."""
        token = auth.token if auth is not None else None
        log.debug(
            f"[set_mode] session_key={request.session_key} mode={request.mode}"
        )
        result = await self._port.approvals_set(
            session_key=request.session_key,
            mode=request.mode,
            token=token,
        )
        if not result.get("ok"):
            error_msg = result.get("error", "Unknown error")
            raise RuntimeError(f"exec.approvals.set failed: {error_msg}")
        payload = result.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        return ApprovalModeSetResult(
            ok=bool(payload.get("ok", True)),
            mode=request.mode,
            session_key=request.session_key,
            payload=payload,
        )


__all__ = ["OpenClawApprovalAdapter"]
