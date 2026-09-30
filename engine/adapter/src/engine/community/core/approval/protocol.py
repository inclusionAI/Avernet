"""ApprovalService Protocol — session-level approval-mode get / set.

Models the user-facing "approval mode" toggle (谨慎 / 半自动 / 自主) that the
frontend exposes via ``/api/approvals/mode/{get,set}``. Engines that support
session-level approval policy (OpenClaw inherited this from the Moltis model)
implement this Protocol; engines that don't (AiCoding gateway has no
``exec.approvals.*`` methods) leave ``Engine.approval`` as ``None`` and the HTTP
route returns 501 via the capability guard.

Decoupled from in-stream approval handling:
  - The mid-stream ``approval.requested`` / ``approval.resolved`` events ride
    the chat stream and stay inside ``ChatService.stream``.
  - The mid-stream ``exec.approval.resolve`` request rides the WS relay path
    via ``RelayService.forward_request``.

This Protocol is purely for the session-level *policy* CRUD — orthogonal to
both of the above.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.approval.models import (
    ApprovalModeGetRequest,
    ApprovalModeGetResult,
    ApprovalModeSetRequest,
    ApprovalModeSetResult,
)
from engine.community.core.engine.context import AuthContext


@runtime_checkable
class ApprovalService(Protocol):
    """Session-level approval-mode service."""

    async def get_mode(
        self,
        request: ApprovalModeGetRequest,
        auth: AuthContext | None = None,
    ) -> ApprovalModeGetResult:
        """Read the current approval mode for the given session.

        Engines should return ``ApprovalModeGetResult.payload`` populated with
        the raw upstream response so legacy callers (HTTP route returning
        engine-specific fields like ``globalDefault`` / ``isOverride``) keep
        working without churn.
        """
        ...

    async def set_mode(
        self,
        request: ApprovalModeSetRequest,
        auth: AuthContext | None = None,
    ) -> ApprovalModeSetResult:
        """Set the approval mode for the given session.

        Implementations should propagate the session key and mode upstream and
        return the engine's ack as ``ApprovalModeSetResult``. Validation of
        ``mode`` against the engine's accepted vocabulary is the engine's
        responsibility (the HTTP layer also pre-validates against a known
        whitelist for early rejection).
        """
        ...


__all__ = ["ApprovalService"]
