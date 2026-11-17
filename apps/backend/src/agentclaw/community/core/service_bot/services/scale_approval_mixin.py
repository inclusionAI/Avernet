"""Approval-gated service-Bot scale operations."""

from __future__ import annotations

import copy

from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)

# The product requirement names these six reviewers for service-Bot scaling.
SERVICE_BOT_SCALE_APPROVER_STAFF_IDS = (
    "342011",  # 启霞
    "113488",  # 贤睦
    "330429",  # 运海
    "074488",  # 慕柳
    "165137",  # 一朴
    "382716",  # 盼祺
)


class ScaleApprovalMixin:
    """Own the scale approval's creation and durable execution branch."""

    async def _trigger_scale(
        self, publish_id: int, applicant: str, *, puid: str | None = None
    ) -> None:
        """Execute the approved scale exactly once with the frozen target."""
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishFlowServiceError(f"Publish not found: {publish_id}")
        ext = copy.deepcopy(publish_record.ext or {})
        approval = ext.get("approval")
        if not isinstance(approval, dict):
            raise PublishFlowServiceError("Scale approval not found")
        if approval.get("action") != "scale":
            raise PublishFlowServiceError("Approval is not a scale approval")
        if not puid:
            raise PublishFlowServiceError("Scale approval trigger missing puid")
        if approval.get("puid") != puid:
            raise PublishFlowServiceError("Scale approval identity changed")
        if approval.get("status") == "EXECUTED":
            return
        if approval.get("status") == "EXECUTING":
            # scale_bot records ext.scale.publish_id before returning; use that
            # marker as well as our own copy to close the crash-after-issue window.
            marker = ext.get("scale") or {}
            persisted_scale_id = marker.get("publish_id")
            marker_belongs_to_approval = marker.get("approval_puid") == puid
            if approval.get("baas_publish_id") or (
                persisted_scale_id and marker_belongs_to_approval
            ):
                self._mark_scale_executed(publish_id)
                return
        if approval.get("status") not in {"AGREED", "EXECUTING"}:
            raise PublishFlowServiceError(
                f"Scale approval is not executable: {approval.get('status')}"
            )
        target_count = approval.get("target_count")
        if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count < 1:
            raise PublishFlowServiceError("Scale approval has no valid target_count")

        expected_ext = copy.deepcopy(publish_record.ext)
        approval["status"] = "EXECUTING"
        ext["approval"] = approval
        self._publish_service.update_publish_ext(
            publish_id, ext, expected_ext=expected_ext
        )

        publish_flow_service = self._publish_flow_service_provider()
        result = await publish_flow_service.scale_bot(
            publish_id=publish_id,
            target_count=target_count,
            operator=approval.get("operator_id") or applicant,
            approval_puid=puid,
        )
        if not result or not result.get("success"):
            raise PublishFlowServiceError("Scale execution failed")

        updated_record = self._publish_service.get_publish_by_id(publish_id)
        if updated_record:
            expected_ext = copy.deepcopy(updated_record.ext)
            current_ext = copy.deepcopy(updated_record.ext or {})
            current_approval = current_ext.get("approval")
            if isinstance(current_approval, dict):
                current_approval["status"] = "EXECUTED"
                current_approval["baas_publish_id"] = result.get("baas_publish_id")
                current_ext["approval"] = current_approval
                self._publish_service.update_publish_ext(
                    publish_id, current_ext, expected_ext=expected_ext
                )

    def _mark_scale_executed(self, publish_id: int) -> None:
        updated_record = self._publish_service.get_publish_by_id(publish_id)
        if not updated_record:
            return
        expected_ext = copy.deepcopy(updated_record.ext)
        current_ext = copy.deepcopy(updated_record.ext or {})
        current_approval = current_ext.get("approval")
        if isinstance(current_approval, dict):
            current_approval["status"] = "EXECUTED"
            current_ext["approval"] = current_approval
            self._publish_service.update_publish_ext(
                publish_id, current_ext, expected_ext=expected_ext
            )
