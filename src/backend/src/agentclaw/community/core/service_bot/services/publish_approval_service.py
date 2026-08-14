"""Service bot publish approval service.

Handles owner approval flow for service bot online/offline operations.
When Bot.ext.service_bot_config.should_approval=True, collaborators must
get Owner approval before publishing or unpublishing.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, Literal, Optional

from agentclaw.community.api.publish_approval import ApprovalResult, PublishApprovalServiceProtocol
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowService
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
    from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin


logger = get_logger()

# approval process code for service bot publish approval
SERVICE_BOT_PUBLISH_PROCESS_CODE = "teamclaw_service_bot_publish"


class PublishApprovalService(PublishApprovalServiceProtocol):
    """Service bot publish approval service.

    Manages the approval workflow for publishing/unpublishing service bots
    when the owner has enabled the should_approval configuration.
    """

    def __init__(
        self,
        publish_service: "BotPublishService",
        publish_flow_service_provider: Callable[[], "PublishFlowService"],
        process_service: "ApprovalWorkflowPlugin",
        bot_service: "BotService",
        task_queue_service: "TaskQueueService",
    ):
        """Initialize the publish approval service.

        Args:
            publish_service: Service for updating publish records
            publish_flow_service_provider: Lazy provider for publish flow service (breaks circular dep)
            process_service: approval-workflow plugin for creating approvals
            bot_service: Bot service for reading bot configuration
            task_queue_service: Durable queue for the AGREED-trigger task (#197)
        """
        self._publish_service = publish_service
        self._publish_flow_service_provider = publish_flow_service_provider
        self._process_service = process_service
        self._bot_service = bot_service
        self._task_queue_service = task_queue_service

    def _is_approval_required(self, publish_record: BotPublishRecord) -> bool:
        """Check if approval is required for this publish record.

        Reads Bot.ext.service_bot_config.should_approval:
        - True: approval required
        - False/None: no approval required (default)

        Args:
            publish_record: The bot publish record

        Returns:
            True if approval is required, False otherwise
        """
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=publish_record.owner_id,
        )
        if not bot:
            # Bot not found, default to no approval required
            logger.warning(
                "[_is_approval_required] Bot not found: bot_id=%s, owner_id=%s, defaulting to no approval",
                publish_record.source_bot_id,
                publish_record.owner_id,
            )
            return False

        bot_ext = bot.get("ext") or {}
        service_bot_config = bot_ext.get("service_bot_config") or {}
        should_approval = service_bot_config.get("should_approval")

        # True -> requires approval, False/None -> no approval
        return should_approval is True

    def _archive_approval(self, publish_record: BotPublishRecord) -> None:
        """Archive current approval to history (max 3 entries).

        Moves ext.approval to ext.approval_history and clears ext.approval.

        Args:
            publish_record: The bot publish record
        """
        expected_ext = copy.deepcopy(publish_record.ext)
        ext = copy.deepcopy(publish_record.ext or {})
        approval = ext.get("approval")

        if not approval:
            return

        logger.info(
            "[_archive_approval] archiving: publish_id=%s, puid=%s, status=%s",
            publish_record.id,
            approval.get("puid"),
            approval.get("status"),
        )

        history = ext.get("approval_history") or []
        history.insert(0, approval)

        # Keep only the latest 3 entries
        if len(history) > 3:
            removed = history[3:]
            history = history[:3]
            logger.info(
                "[_archive_approval] removed old history: publish_id=%s, count=%d",
                publish_record.id,
                len(removed),
            )

        ext["approval_history"] = history
        ext["approval"] = None
        self._publish_service.update_publish_ext(
            publish_record.id, ext, expected_ext=expected_ext
        )

        logger.info(
            "[_archive_approval] archived: publish_id=%s, history_count=%d",
            publish_record.id,
            len(history),
        )

    async def _create_new_approval(
        self,
        publish_record: BotPublishRecord,
        action: Literal["online", "offline"],
        operator: str,
    ) -> ApprovalResult:
        """Create a new approval request.

        Args:
            publish_record: The bot publish record
            action: "online" or "offline"
            operator: The operator's user ID

        Returns:
            ApprovalResult with the new approval status
        """
        # Get bot info for approval context
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=publish_record.owner_id,
        )
        if not bot:
            logger.error(
                "[_create_new_approval] Bot not found: publish_id=%s, bot_id=%s",
                publish_record.id,
                publish_record.source_bot_id,
            )
            return ApprovalResult(
                should_approval=True,
                status="ERROR",
                approval=None,
                message=f"Bot not found: {publish_record.source_bot_id}",
            )

        owner_id = publish_record.owner_id
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        logger.info(
            "[_create_new_approval] start: publish_id=%s, action=%s, operator=%s, owner=%s",
            publish_record.id,
            action,
            operator,
            owner_id,
        )

        publish_record = self._archive_if_terminal(publish_record)

        # Approval does NOT go through the operation ledger: ``start_approval`` opens
        # an antprocess approval instance (the approval-workflow plugin), not a BaaS
        # workflow, so there is nothing to adopt-by-query and the ledger's crash-safe
        # resolution does not apply. Idempotency here is the caller's PROCESSING
        # guard (``check_and_process_*`` returns the existing approval) plus the puid
        # persisted in ``ext.approval``. A crash after ``start_approval`` but before
        # the ext write is a bounded orphan either way (a re-run opens a new approval
        # instance); a ledger row would only observe it, not prevent it.
        biz_id = f"{publish_record.id}{action}{timestamp}"
        approval_result = self._start_approval_workflow(
            publish_record, action=action, operator=operator, biz_id=biz_id
        )

        if not approval_result.get("success"):
            error_msg = approval_result.get("error_msg", "Unknown error")
            logger.error(
                "[_create_new_approval] failed: publish_id=%s, error=%s",
                publish_record.id,
                error_msg,
            )
            return ApprovalResult(
                should_approval=True,
                status="ERROR",
                approval=None,
                message=f"创建审批失败: {error_msg}",
            )

        return self._finalize_new_approval(
            publish_record,
            approval_result=approval_result,
            action=action,
            operator=operator,
        )

    def _finalize_new_approval(
        self,
        publish_record: BotPublishRecord,
        *,
        approval_result: Dict[str, Any],
        action: str,
        operator: str,
    ) -> ApprovalResult:
        """Write ``ext.approval`` (with the puid) and return the PROCESSING result."""
        puid = approval_result.get("puid")

        new_approval = {
            "puid": puid,
            "action": action,
            "operator_id": operator,
            "status": "PROCESSING",
            "owner_id": publish_record.owner_id,
            "approval_url": approval_result.get("approval_url"),
            "created_at": datetime.now().isoformat(),
        }
        expected_ext = copy.deepcopy(publish_record.ext)
        ext = copy.deepcopy(publish_record.ext or {})
        ext["approval"] = new_approval
        self._publish_service.update_publish_ext(
            publish_record.id, ext, expected_ext=expected_ext
        )

        logger.info(
            "[_create_new_approval] created: publish_id=%s, puid=%s",
            publish_record.id,
            puid,
        )

        return ApprovalResult(
            should_approval=True,
            status="PROCESSING",
            approval=new_approval,
            message="审批已创建，请等待审批结果",
        )

    def _archive_if_terminal(self, publish_record: BotPublishRecord) -> BotPublishRecord:
        """Archive the current approval to history if it is in a terminal state,
        returning the refreshed record (so the fresh approval writes onto clean
        ext). A non-terminal / absent approval is left untouched."""
        approval = (publish_record.ext or {}).get("approval")
        current_status = approval.get("status") if approval else None
        if current_status in ("AGREED", "DISAGREED", "CANCEL", "EXECUTED"):
            self._archive_approval(publish_record)
            updated_record = self._publish_service.get_publish_by_id(publish_record.id)
            if updated_record:
                return updated_record
        return publish_record

    def _start_approval_workflow(
        self,
        publish_record: BotPublishRecord,
        *,
        action: Literal["online", "offline"],
        operator: str,
        biz_id: str,
    ) -> Dict[str, Any]:
        """Call the approval-workflow plugin to open the approval instance,
        assembling the auditor context. Returns the plugin's raw result dict
        (carries ``success`` / ``puid`` / ``approval_url`` / ``error_msg``)."""
        owner_id = publish_record.owner_id
        owner_name = publish_record.owner_name or owner_id
        publish_name = publish_record.name or publish_record.source_bot_id
        return self._process_service.start_approval(
            applicant=operator,
            biz_id=biz_id,
            process_code=SERVICE_BOT_PUBLISH_PROCESS_CODE,
            biz_type=f"botpublish4{action}",
            context={
                "publish_id": str(publish_record.id),
                "action": action,
                "applicant": operator,
                "publish_owner_audit": f"w[{owner_id}]",  # approval format for auditor
                "owner_name": owner_name,
                "publish_name": publish_name,
                "content": f"{publish_name} 线上发布审批" if action == "online" else f"{publish_name} 下线审批",
            },
        )

    async def check_and_process_should_approval(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> ApprovalResult:
        """Check and process online (publish) approval.

        Args:
            publish_record: The bot publish record
            operator: The operator's user ID

        Returns:
            ApprovalResult indicating whether to stop or continue
        """
        logger.info(
            "[check_and_process_should_approval] start: publish_id=%s, operator=%s",
            publish_record.id,
            operator,
        )

        # 1. Check if approval is required
        if not self._is_approval_required(publish_record):
            logger.info(
                "[check_and_process_should_approval] skip: publish_id=%s, should_approval=False",
                publish_record.id,
            )
            return ApprovalResult(
                should_approval=False,
                status="SKIP",
                approval=None,
                message="无需审批，直接执行上线",
            )

        # 2. Get current approval and status
        ext = copy.deepcopy(publish_record.ext or {})
        approval = ext.get("approval")
        current_status = approval.get("status") if approval else None

        logger.info(
            "[check_and_process_should_approval] current_status=%s, publish_id=%s",
            current_status,
            publish_record.id,
        )

        # 3. Make decision based on status
        if current_status == "PROCESSING":
            # Return existing approval
            logger.info(
                "[check_and_process_should_approval] return existing: publish_id=%s, puid=%s",
                publish_record.id,
                approval.get("puid") if approval else None,
            )
            return ApprovalResult(
                should_approval=True,
                status="PROCESSING",
                approval=approval,
                message="审批中，请等待审批结果",
            )

        # 4. If operator is the owner, skip approval
        if operator == publish_record.owner_id:
            logger.info(
                "[check_and_process_should_approval] skip: operator is owner, publish_id=%s, operator=%s",
                publish_record.id,
                operator,
            )
            return ApprovalResult(
                should_approval=False,
                status="SKIP",
                approval=None,
                message="无需审批，操作者为 Bot 拥有者",
            )

        # Create new approval
        return await self._create_new_approval(
            publish_record=publish_record,
            action="online",
            operator=operator,
        )

    async def check_and_process_offline_approval(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> ApprovalResult:
        """Check and process offline (unpublish) approval.

        Args:
            publish_record: The bot publish record
            operator: The operator's user ID

        Returns:
            ApprovalResult indicating whether to stop or continue
        """
        logger.info(
            "[check_and_process_offline_approval] start: publish_id=%s, operator=%s",
            publish_record.id,
            operator,
        )

        # 1. Check if approval is required
        if not self._is_approval_required(publish_record):
            logger.info(
                "[check_and_process_offline_approval] skip: publish_id=%s, should_approval=False",
                publish_record.id,
            )
            return ApprovalResult(
                should_approval=False,
                status="SKIP",
                approval=None,
                message="无需审批，直接执行下线",
            )

        # 2. Get current approval and status
        ext = publish_record.ext or {}
        approval = ext.get("approval")
        current_status = approval.get("status") if approval else None

        logger.info(
            "[check_and_process_offline_approval] current_status=%s, publish_id=%s",
            current_status,
            publish_record.id,
        )

        # 3. Make decision based on status
        if current_status == "PROCESSING":
            # Return existing approval
            logger.info(
                "[check_and_process_offline_approval] return existing: publish_id=%s, puid=%s",
                publish_record.id,
                approval.get("puid") if approval else None,
            )
            return ApprovalResult(
                should_approval=True,
                status="PROCESSING",
                approval=approval,
                message="审批中，请等待审批结果",
            )

        # 4. If operator is the owner, skip approval
        if operator == publish_record.owner_id:
            logger.info(
                "[check_and_process_offline_approval] skip: operator is owner, publish_id=%s, operator=%s",
                publish_record.id,
                operator,
            )
            return ApprovalResult(
                should_approval=False,
                status="SKIP",
                approval=None,
                message="无需审批，操作者为 Bot 拥有者",
            )

        # Create new approval
        return await self._create_new_approval(
            publish_record=publish_record,
            action="offline",
            operator=operator,
        )

    async def handle_approval_callback(
        self,
        publish_id: int,
        action: str,
        applicant: str,
        puid: str,
        last_operate: str,
    ) -> Dict[str, Any]:
        """Handle approval callback.

        Args:
            publish_id: The publish record ID
            action: "online" or "offline"
            applicant: The applicant's user ID
            puid: The approval instance ID
            last_operate: The approval result ("AGREE" | "DISAGREE" | "CANCEL")

        Returns:
            Dict with success status and message
        """
        logger.info(
            "[handle_approval_callback] start: publish_id=%s, action=%s, applicant=%s, last_operate=%s",
            publish_id,
            action,
            applicant,
            last_operate,
        )

        # 1. Query publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.error("[handle_approval_callback] publish not found: %s", publish_id)
            return {"success": False, "message": f"Publish not found: {publish_id}"}

        # 2. Validate approval record
        expected_ext = copy.deepcopy(publish_record.ext)
        ext = copy.deepcopy(publish_record.ext or {})
        approval = ext.get("approval")

        if not approval:
            logger.error("[handle_approval_callback] no approval: publish_id=%s", publish_id)
            return {"success": False, "message": "No approval found"}

        if approval.get("puid") != puid:
            logger.warning(
                "[handle_approval_callback] puid mismatch: publish_id=%s, expected=%s, got=%s",
                publish_id,
                approval.get("puid"),
                puid,
            )
            return {"success": False, "message": "PUID mismatch"}

        # 3. Update status
        status_map = {"AGREE": "AGREED", "DISAGREE": "DISAGREED", "CANCEL": "CANCEL"}
        new_status = status_map.get(last_operate.upper())
        if not new_status:
            logger.error(
                "[handle_approval_callback] unknown last_operate: %s",
                last_operate,
            )
            return {"success": False, "message": f"Unknown last_operate: {last_operate}"}

        approval["status"] = new_status
        approval["processed_at"] = datetime.now().isoformat()
        ext["approval"] = approval
        self._publish_service.update_publish_ext(
            publish_id, ext, expected_ext=expected_ext
        )

        logger.info(
            "[handle_approval_callback] updated: publish_id=%s, status=%s",
            publish_id,
            new_status,
        )

        # 4. Trigger follow-up actions
        if new_status == "AGREED":
            early = self._enqueue_agreed_trigger(
                publish_record, action=action, applicant=applicant, approval=approval
            )
            if early is not None:
                return early

        return {"success": True, "message": f"Approval {new_status}"}

    def _enqueue_agreed_trigger(
        self,
        publish_record: BotPublishRecord,
        *,
        action: str,
        applicant: str,
        approval: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """On an AGREED callback, enqueue the durable trigger for the follow-up
        publish/offline. Returns an early-return response dict when the record's
        status no longer matches the action (nothing to trigger), else None.

        (#197) Enqueuing instead of awaiting inline lets the callback return fast;
        an AGREED-then-crash still converges (the task retries) and a duplicate
        callback is a no-op via the trigger's status-CAS-guarded process()/offline.
        The status precheck here is only for fast feedback."""
        publish_id = publish_record.id
        logger.info(
            "[handle_approval_callback] triggering action: publish_id=%s, action=%s",
            publish_id,
            action,
        )
        operator_id = approval.get("operator_id", applicant)
        expected = {
            "online": PublishStatus.VALIDATING,
            "offline": PublishStatus.SUCCESS,
        }.get(action)
        if expected is not None and publish_record.status != expected:
            logger.warning(
                "[handle_approval_callback] invalid status for %s: publish_id=%s, status=%s, expected=%s",
                action, publish_id, publish_record.status, expected,
            )
            return {"success": True, "message": f"Approval AGREED but status is {publish_record.status}"}

        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            enqueue_approval_trigger,
        )

        enqueue_approval_trigger(
            self._task_queue_service,
            publish_id=publish_id,
            action=action,
            operator=operator_id,
        )
        return None

    async def execute_approval_trigger(
        self,
        publish_id: int,
        action: str,
        operator: str,
    ) -> dict:
        """Durable AGREED-trigger work (#197): dispatch to the online-release or
        offline trigger. Both re-validate the approval is AGREED and drive the
        status-CAS-guarded publish op, so a re-run (duplicate callback / lease
        re-claim) is a no-op. Returns ``{success, message}``."""
        if action == "online":
            await self._trigger_online_release(publish_id, operator)
        elif action == "offline":
            await self._trigger_offline(publish_id, operator)
        else:
            return {"success": False, "message": f"Unknown approval action: {action}"}
        return {"success": True, "message": f"Approval trigger {action} executed"}

    async def _trigger_online_release(self, publish_id: int, applicant: str) -> None:
        """Trigger online release after approval is agreed.

        Args:
            publish_id: The publish record ID
            applicant: The applicant's user ID
        """
        logger.info(
            "[_trigger_online_release] start: publish_id=%s, applicant=%s",
            publish_id,
            applicant,
        )

        # 1. Query publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.error("[_trigger_online_release] publish not found: %s", publish_id)
            return

        # 2. Validate approval
        ext = publish_record.ext or {}
        approval = ext.get("approval")

        if not approval:
            logger.error("[_trigger_online_release] no approval found: publish_id=%s", publish_id)
            return

        if approval.get("status") != "AGREED":
            logger.warning(
                "[_trigger_online_release] status not AGREED: publish_id=%s, applicant=%s, status=%s",
                publish_id,
                applicant,
                approval.get("status"),
            )
            return

        # 3. Trigger online release
        try:
            publish_flow_service = self._publish_flow_service_provider()
            result = await publish_flow_service.process(
                publish_id=publish_id,
                operator=applicant,
            )
            logger.info(
                "[_trigger_online_release] completed: publish_id=%s, result_status=%s",
                publish_id,
                result.status,
            )

            # 4. Mark as EXECUTED only after successful execution
            if result.status in (PublishStatus.SUCCESS, PublishStatus.ONLINE_PUB):
                # Re-fetch to get latest ext
                updated_record = self._publish_service.get_publish_by_id(publish_id)
                if updated_record:
                    expected_ext = copy.deepcopy(updated_record.ext)
                    ext = copy.deepcopy(updated_record.ext or {})
                    approval = ext.get("approval")
                    if approval:
                        approval["status"] = "EXECUTED"
                        ext["approval"] = approval
                        self._publish_service.update_publish_ext(
                            publish_id, ext, expected_ext=expected_ext
                        )
                        logger.info(
                            "[_trigger_online_release] marked EXECUTED: publish_id=%s",
                            publish_id,
                        )
        except Exception as e:
            logger.error(
                "[_trigger_online_release] failed: publish_id=%s, error=%s",
                publish_id,
                e,
            )

    async def _trigger_offline(self, publish_id: int, applicant: str) -> None:
        """Trigger offline after approval is agreed.

        Args:
            publish_id: The publish record ID
            applicant: The applicant's user ID
        """
        logger.info(
            "[_trigger_offline] start: publish_id=%s, applicant=%s",
            publish_id,
            applicant,
        )

        # 1. Query publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.error("[_trigger_offline] publish not found: %s", publish_id)
            return

        # 2. Validate approval
        ext = publish_record.ext or {}
        approval = ext.get("approval")

        if not approval:
            logger.error("[_trigger_offline] no approval found: publish_id=%s", publish_id)
            return

        if approval.get("status") != "AGREED":
            logger.warning(
                "[_trigger_offline] status not AGREED: publish_id=%s, applicant=%s, status=%s",
                publish_id,
                applicant,
                approval.get("status"),
            )
            return

        # 3. Trigger offline
        try:
            result = await self._publish_service.offline_publish(
                publish_id=publish_id,
            )
            logger.info(
                "[_trigger_offline] completed: publish_id=%s, result=%s",
                publish_id,
                result,
            )

            # 4. Mark as EXECUTED only after successful execution
            if result:
                # Re-fetch to get latest ext
                updated_record = self._publish_service.get_publish_by_id(publish_id)
                if updated_record:
                    expected_ext = copy.deepcopy(updated_record.ext)
                    ext = copy.deepcopy(updated_record.ext or {})
                    approval = ext.get("approval")
                    if approval:
                        approval["status"] = "EXECUTED"
                        ext["approval"] = approval
                        self._publish_service.update_publish_ext(
                            publish_id, ext, expected_ext=expected_ext
                        )
                        logger.info(
                            "[_trigger_offline] marked EXECUTED: publish_id=%s",
                            publish_id,
                        )
        except Exception as e:
            logger.error(
                "[_trigger_offline] failed: publish_id=%s, error=%s",
                publish_id,
                e,
            )
