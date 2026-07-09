"""Capability Protocol for an external bot-publish approval workflow.

Implementations start, query, and cancel approval instances. The corp
implementation talks to the company workflow platform; the community
implementation reports "no workflow" so callers fall through to direct
publish; the local noop returns the same sentinel for offline dev.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


DEFAULT_PROCESS_CODE = "agentclaw_bot_publish"


@runtime_checkable
class ApprovalWorkflowPlugin(Plugin, Protocol):
    """Abstracts the bot-publish approval workflow.

    Result shapes are dicts with at minimum a ``success`` boolean.
    Implementations backed by a real workflow service populate ``puid`` /
    ``approval_url`` / ``state`` on success; an impl with no workflow returns
    ``success=False`` with an ``error_msg`` explaining the unavailability.
    """

    def start_approval(
        self,
        applicant: str,
        biz_id: str,
        process_code: str = DEFAULT_PROCESS_CODE,
        biz_type: Optional[str] = None,
        unique_key: Optional[str] = None,
        context: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Kick off an approval workflow.

        Returns:
            - Success: ``{"success": True, "puid": ..., "approval_url": ...,
                          "state": ..., "lastOperate": ..., "error_msg": None}``.
            - Failure / rejection: ``{"success": False, "puid": None,
                                       "approval_url": None, "error_msg": <reason>}``.
            - No workflow available: ``{"success": False, "puid": None,
                        "approval_url": None, "error_msg": <reason-unavailable>}``.
        """
        ...

    def query_approval_status(self, puid: str) -> dict[str, Any]:
        """Query the current status of an approval instance.

        Local impl returns a sentinel marking the query as unavailable
        so any local-mode caller can short-circuit instead of polling
        forever.
        """
        ...

    def cancel_approval(self, puid: str, operator: str) -> bool:
        """Cancel an approval. Local impl returns False (nothing to cancel)."""
        ...
