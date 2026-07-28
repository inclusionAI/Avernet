"""Draft restore operations for the service-bot publish flow."""

from __future__ import annotations

import copy
from datetime import datetime

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    DraftRestoreRetryableError,
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    to_baas_request_id,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger


logger = get_logger()

_DRAFT_RESTORE_TIMEOUT_SECONDS = 1800


class DraftRestoreOpsMixin:
    """Restore a historical artifact into the editable draft container."""

    async def execute_restore_draft(
        self,
        *,
        draft_publish_id: int,
        operation_id: int,
        operator: str,
    ) -> dict:
        """Restore the draft without advancing the publish state machine."""
        draft = self._publish_service.get_publish_by_id(draft_publish_id)
        operation = self._publish_operation_repo.get_by_id(operation_id)
        if not operation:
            raise PublishFlowServiceError(
                f"草稿恢复 operation 不存在: operation_id={operation_id}"
            )
        if (
            operation.publish_id != draft_publish_id
            or operation.operation_kind != str(PublishOperationKind.DRAFT_RESTORE)
            or operation.stage != PublishStage.DRAFT.value
        ):
            raise PublishFlowServiceError(
                "草稿恢复 operation 与当前发布单不匹配: "
                f"operation_id={operation_id}, publish_id={draft_publish_id}"
            )
        if operation.state == PublishOperationState.COMPLETED.value:
            # At-least-once task delivery can replay after the ledger completed
            # but before the queue row was marked SUCCEEDED. Treat the durable
            # operation result as authoritative and do not touch BaaS/files again.
            return {**(operation.result or {}), "status": "success"}
        if operation.state == PublishOperationState.FAILED.value:
            raise PublishFlowServiceError(
                operation.last_error or f"草稿恢复 operation 已失败: {operation_id}"
            )
        if operation.state == PublishOperationState.ABANDONED.value:
            raise PublishFlowServiceError(
                operation.last_error or f"草稿恢复 operation 已废弃: {operation_id}"
            )
        # Validation/local-file failures are deterministic and close the attempt.
        # Once a BaaS workflow acquire begins, however, an exception can mean the
        # mutation landed remotely but its response/workflow id was not persisted.
        # Keep that operation non-terminal so the task retries the SAME attempt and
        # acquire_workflow can resolve the in-doubt window via adopt-by-query.
        preserve_nonterminal_on_error = False
        try:
            if operation.state not in {
                PublishOperationState.PENDING.value,
                PublishOperationState.ID_RECORDED.value,
            }:
                raise PublishFlowServiceError(
                    "草稿恢复 operation 状态不可执行: "
                    f"operation_id={operation_id}, state={operation.state}"
                )
            if not draft:
                raise PublishNotFoundError(f"Draft publish not found: {draft_publish_id}")
            if draft.status != PublishStatus.DRAFT:
                raise PublishStatusInvalidError(
                    f"Only DRAFT can be restored, got {draft.status}"
                )

            operation_params = operation.params or {}
            deadline_at_raw = operation_params.get("deadline_at")
            try:
                if not isinstance(deadline_at_raw, str) or not deadline_at_raw:
                    raise ValueError("deadline_at is missing")
                deadline_at = datetime.fromisoformat(deadline_at_raw)
            except (TypeError, ValueError) as exc:
                error = "草稿恢复 operation 缺少有效的 deadline_at"
                raise PublishFlowServiceError(error) from exc
            now = datetime.now(tz=deadline_at.tzinfo)
            if now >= deadline_at:
                error = "恢复草稿超时（默认限制 30 分钟）"
                raise PublishFlowServiceError(error)

            source_publish_id = operation_params.get("source_publish_id")
            source_version = operation_params.get("source_version")
            if (
                isinstance(source_publish_id, bool)
                or not isinstance(source_publish_id, int)
                or source_publish_id <= 0
                or isinstance(source_version, bool)
                or not isinstance(source_version, int)
                or source_version <= 0
            ):
                raise PublishFlowServiceError(
                    "草稿恢复 operation 缺少有效的 source_publish_id/source_version: "
                    f"operation_id={operation_id}"
                )
            if draft.last_pub_id != source_publish_id:
                raise PublishFlowServiceError(
                    "当前草稿的上一版本已变化，请重新发起恢复: "
                    f"operation_id={operation_id}, source_publish_id={source_publish_id}, "
                    f"current_last_pub_id={draft.last_pub_id}"
                )
            source = self._publish_service.get_publish_by_id(source_publish_id)
            if not source:
                raise PublishNotFoundError(f"Source publish not found: {source_publish_id}")
            if source.source_bot_pk != draft.source_bot_pk or source.env != draft.env:
                raise PublishFlowServiceError("上一版本与当前草稿不属于同一个 Bot 或环境")
            if source.version != source_version:
                raise PublishFlowServiceError(
                    "草稿恢复 operation 记录的来源版本与发布单不一致: "
                    f"operation_id={operation_id}, source_publish_id={source_publish_id}"
                )

            source_ext = copy.deepcopy(source.ext or {})
            owner_id = self._get_owner_id(draft)
            bot = self._bot_service.get_bot(
                bot_id=draft.source_bot_id,
                user_id=owner_id,
            )
            if not bot:
                raise PublishFlowServiceError(f"Bot不存在: {draft.source_bot_id}")

            binding_id = bot.get("binding_id")
            if not binding_id:
                raise PublishFlowServiceError("草稿 Bot 缺少 binding_id")
            binding = self._publish_service.get_device_binding_by_id(binding_id)
            if not binding or not binding.device_id:
                raise PublishFlowServiceError(
                    "草稿设备绑定不存在或缺少 device_id: "
                    f"binding_id={binding_id}"
                )
            if binding.status != DeviceBindingStatus.ACTIVE.value:
                raise PublishFlowServiceError(
                    f"草稿容器未就绪: binding_id={binding_id}, status={binding.status}"
                )

            behavior = self._provider_behavior(bot)
            invalid_reason = behavior.validate_draft_restore_artifact(source_ext)
            if invalid_reason:
                raise PublishFlowServiceError(invalid_reason)

            if behavior.draft_restore_uses_workflow:
                if operation.state == PublishOperationState.PENDING.value:
                    if operation.bot_uuid != binding.device_id:
                        raise PublishFlowServiceError(
                            "草稿恢复 operation 的 bot_uuid 与当前绑定不一致: "
                            f"operation_id={operation_id}"
                        )

                    async def _issue() -> dict:
                        return await behavior.restore_draft(
                            build_service=self._build_service,
                            bot=bot,
                            bot_uuid=binding.device_id,
                            owner_id=owner_id,
                            source_version=source_version,
                            artifact_ext=source_ext,
                            request_id=to_baas_request_id(operation.request_id),
                        )

                    preserve_nonterminal_on_error = True
                    operation = await self._operation_runner.acquire_workflow(
                        operation, _issue
                    )

                if operation.baas_publish_id is None:
                    preserve_nonterminal_on_error = False
                    raise PublishFlowServiceError(
                        f"草稿恢复 operation 缺少 BaaS publish_id: {operation_id}"
                    )
                preserve_nonterminal_on_error = True
                result = await behavior.restore_draft(
                    build_service=self._build_service,
                    bot=bot,
                    bot_uuid=binding.device_id,
                    owner_id=owner_id,
                    source_version=source_version,
                    artifact_ext=source_ext,
                    baas_publish_id=operation.baas_publish_id,
                )
            else:
                if operation.state != PublishOperationState.PENDING.value:
                    raise PublishFlowServiceError(
                        "本地草稿恢复 operation 状态不可续跑: "
                        f"operation_id={operation_id}, state={operation.state}"
                    )
                result = await behavior.restore_draft(
                    build_service=self._build_service,
                    bot=bot,
                    bot_uuid=binding.device_id,
                    owner_id=owner_id,
                    source_version=source_version,
                    artifact_ext=source_ext,
                )

            if result.get("status") == "failed":
                # A terminal status returned by BaaS is a certain business
                # failure, not an in-doubt transport error. Close the ledger row
                # and let the task become terminally FAILED.
                preserve_nonterminal_on_error = False
                raise PublishFlowServiceError(
                    str(result.get("error") or "恢复草稿失败")
                )

            if result.get("status") == "restoring":
                return {
                    "draft_binding_id": binding_id,
                    **result,
                }

            final_result = {
                "draft_binding_id": binding_id,
                "status": "success",
                **result,
            }
            self._publish_operation_repo.update_result(operation_id, final_result)
            if operation.state == PublishOperationState.PENDING.value:
                completed = self._publish_operation_repo.complete_without_workflow(
                    operation_id
                )
            else:
                completed = self._publish_operation_repo.complete(operation_id)
            if completed is None:
                current = self._publish_operation_repo.get_by_id(operation_id)
                if current is None or current.state != PublishOperationState.COMPLETED.value:
                    raise PublishFlowServiceError(
                        "草稿恢复 operation 无法完成: "
                        f"operation_id={operation_id}, state={getattr(current, 'state', None)}"
                    )
            result = final_result

            logger.info(
                "[DraftRestoreOpsMixin.execute_restore_draft] restored: "
                "draft_publish_id=%s source_publish_id=%s binding_id=%s type=%s",
                draft_publish_id,
                source_publish_id,
                binding_id,
                result.get("restore_type"),
            )
            return result
        except Exception as exc:
            if preserve_nonterminal_on_error:
                raise DraftRestoreRetryableError(str(exc)) from exc
            self._publish_operation_repo.fail(operation_id, str(exc))
            raise
