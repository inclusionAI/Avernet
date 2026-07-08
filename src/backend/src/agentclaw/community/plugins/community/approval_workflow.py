"""Community ``ApprovalWorkflowPlugin`` — no approval workflow.

A real, deployable impl (not a ``MockSeam`` test double). The community build has
no approval-workflow service. Service-bot publishing proceeds without approval
(the ``BotPublishApprovalPlugin`` strategy publishes directly), so this plugin is
only reached by secondary paths (the friend-request flow + the HTTP passthrough).
It reports "no workflow" so those callers fall through gracefully rather than
registering an approval that would wait forever for a callback that can't arrive.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.plugin_api.approval_workflow import (
    ApprovalWorkflowPlugin,
    DEFAULT_PROCESS_CODE,
)

_NO_WORKFLOW = "no approval workflow in the community build"


class NoApprovalWorkflow(ApprovalWorkflowPlugin):
    """Community profile: approval workflow is not available."""

    def start_approval(
        self,
        applicant: str,
        biz_id: str,
        process_code: str = DEFAULT_PROCESS_CODE,
        biz_type: Optional[str] = None,
        unique_key: Optional[str] = None,
        context: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "puid": None,
            "approval_url": None,
            "state": None,
            "lastOperate": None,
            "error_msg": _NO_WORKFLOW,
        }

    def query_approval_status(self, puid: str) -> dict[str, Any]:
        return {
            "success": False,
            "status": None,
            "last_operate": None,
            "title": None,
            "applicant": None,
            "process_id": None,
            "error_msg": _NO_WORKFLOW,
        }

    def cancel_approval(self, puid: str, operator: str) -> bool:
        return False
