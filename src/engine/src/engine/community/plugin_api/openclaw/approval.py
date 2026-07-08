"""OpenClawApprovalPort — native port for session approval-mode operations.

Approval is pooled (client+pool), so port methods take `token: str | None = None`
for per-token routing.  Returns raw dicts — the adapter builds the core
`ApprovalModeGetResult` / `ApprovalModeSetResult` DTOs and handles !ok errors.
"""
from __future__ import annotations

from typing import Any, Protocol


class OpenClawApprovalPort(Protocol):
    """Native approval-mode operations over the OpenClaw gateway."""

    async def approvals_get(
        self,
        session_key: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `exec.approvals.get` and return `{"ok": bool, "payload": dict}`.

        Returns the raw payload dict on success (`ok=True`). On gateway error
        returns `{"ok": False, "error": <message str>}`.
        """
        ...

    async def approvals_set(
        self,
        session_key: str,
        mode: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `exec.approvals.set` and return `{"ok": bool, "payload": dict}`.

        Returns the raw payload dict on success (`ok=True`). On gateway error
        returns `{"ok": False, "error": <message str>}`.
        """
        ...


__all__ = ["OpenClawApprovalPort"]
