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

# Marker prepended to ``error_msg`` by no-workflow impls (community
# ``NoApprovalWorkflow``, local ``LocalAntProcessService``) so callers can tell
# "this profile has no approval capability" apart from "the workflow service
# rejected this request". The protocol's contract promises the former case is a
# fall-through to direct publish, not a failure; ``BotPublicService.public_bcs_bot``
# relies on matching this prefix to avoid raising 500 in the community build.
NO_WORKFLOW_MARKER = "[no-approval-workflow] "


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
                        Implementations with no workflow prepend
                        ``NO_WORKFLOW_MARKER`` to ``error_msg`` so callers can
                        treat "capability unavailable" as a fall-through, not
                        a failure.
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
