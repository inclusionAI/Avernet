"""Local ``ApprovalWorkflowPlugin`` — noop impl for offline / single-box dev.

The real antprocess flow platform talks to MOSN/Layotto which isn't
reachable on a dev laptop. Local impl returns ``{"success": False,
"error_msg": "local mode — antprocess unavailable"}`` so callers can
treat it uniformly via the standard success/error path.

In practice the bot-publish flow shouldn't reach this impl: the
``BotPublishApprovalPlugin`` strategy's local impl publishes directly
without ever calling ``start_approval``. This noop is a safety net
for any unforeseen caller.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.approval_workflow import (
    ApprovalWorkflowPlugin,
    DEFAULT_PROCESS_CODE,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

logger = get_logger()


_LOCAL_NOT_STARTED: dict[str, Any] = {
    "success": False,
    "puid": None,
    "approval_url": None,
    "state": None,
    "lastOperate": None,
    "error_msg": "local mode — antprocess unavailable",
}


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O in method bodies",
)
class LocalAntProcessService(MockSeam, ApprovalWorkflowPlugin):
    """No-op antprocess service for local mode.

    No constructor deps (the prod impl needs MOSN/Layotto handles; we
    have nothing to wire).
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
        logger.info(
            "[LocalAntProcessService.start_approval] noop — applicant=%s "
            "biz_id=%s process_code=%s",
            applicant, biz_id, process_code,
        )
        return dict(_LOCAL_NOT_STARTED)

    def query_approval_status(self, puid: str) -> dict[str, Any]:
        logger.debug(
            "[LocalAntProcessService.query_approval_status] noop puid=%s", puid,
        )
        return {
            "success": False,
            "status": None,
            "last_operate": None,
            "title": None,
            "applicant": None,
            "process_id": None,
            "error_msg": "local mode — antprocess unavailable",
        }

    def cancel_approval(self, puid: str, operator: str) -> bool:
        logger.debug(
            "[LocalAntProcessService.cancel_approval] noop puid=%s operator=%s",
            puid, operator,
        )
        return False
