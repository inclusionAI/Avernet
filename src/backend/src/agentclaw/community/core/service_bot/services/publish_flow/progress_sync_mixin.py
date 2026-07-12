"""Progress-sync mixin for the publish flow.

The BaaS-publish progress polling and status advancement — the ``/sync``,
``/scale/status`` and ``/restart_status`` entry points plus their SUCCESS/FAILURE
handlers and device-binding updates — split out of ``PublishFlowService`` as a
mixin. It shares ``self`` with the facade (same instance, same collaborators), so
the bodies are unchanged and stay interceptable by tests. Mixin composition keeps
each concern in its own file without threading a context object.
"""
from __future__ import annotations

from typing import Any, Dict

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class ProgressSyncMixin:
    """BaaS progress sync + status advancement (mixed into PublishFlowService)."""

    def _update_binding_on_success(
        self,
        ext: dict,
        stage: PublishStage,
        progress: dict,
        baas_status: str,
        baas_publish_id: int | str,
        bot_id: str,
    ) -> None:
        """更新 device_binding 为成功状态。

        Args:
            ext: 扩展字段
            stage: 发布阶段（VERIFY/ONLINE）
            progress: BaaS 发布进度信息
            baas_status: BaaS 发布状态
            baas_publish_id: BaaS 发布单 ID
            bot_id: Bot ID
        """
        # 从扩展字段获取 binding_id
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService._update_binding_on_success] "
                f"No binding_id found for stage={stage.value}"
            )
            return

        device_details = progress.get("device_details", [])
        device_props = {
            "bolt_id":bot_id,
            "device_details": device_details,
            "baas_status": baas_status,
            "baas_publish_id": baas_publish_id,
            "overall_progress": progress.get("overall_progress", {}),
        }

        self._publish_service.update_device_binding_with_props(
            binding_id=binding_id,
            status=DeviceBindingStatus.ACTIVE,
            device_props=device_props,
        )

        logger.info(
            f"[PublishFlowService._update_binding_on_success] "
            f"Device binding updated: binding_id={binding_id}, status=ACTIVE"
        )

    def _handle_sync_success(
        self,
        publish_id: int,
        publish_record: BotPublishRecord,
        stage: PublishStage,
        current_status: PublishStatus,
        ext: dict,
        baas_publish_id: int | str,
        progress: dict,
    ) -> PublishFlowResult:
        """处理 BaaS 发布成功。

        Args:
            publish_id: 发布单 ID
            publish_record: 发布单记录
            stage: 发布阶段（VERIFY/ONLINE）
            current_status: 当前状态
            ext: 扩展字段
            baas_publish_id: BaaS 发布单 ID
            progress: BaaS 发布进度信息

        Returns:
            PublishFlowResult: 同步结果
        """
        baas_status = progress.get("status", "")

        # 确定目标状态
        if stage == PublishStage.VERIFY:
            target_status = PublishStatus.VALIDATING
        else:
            target_status = PublishStatus.SUCCESS

        # 更新发布单扩展属性值，去掉重试标识
        ext.pop("retry", None)

        # 原子更新：同时更新状态和扩展字段，避免 update_publish_status + update_publish_ext
        # 两步操作之间的竞态条件（TOCTOU问题：先读状态再以该状态为乐观锁更新，
        # 期间状态可能被并发请求修改，导致更新失败）
        self._publish_service.update_publish_status_with_ext(
            publish_id=publish_id,
            target_status=target_status,
            ext=ext,
            source_status=current_status,
        )

        logger.info(
            f"[PublishFlowService._handle_sync_success] "
            f"Publish status updated: {current_status} -> {target_status}"
        )

        # 如果是 online 阶段成功，更新上一个发布单状态为 UPGRADED
        self._mark_previous_publish_superseded(publish_record, stage, target_status)

        # 更新 device_binding 状态为 ACTIVE
        self._update_binding_on_success(
            ext=ext,
            stage=stage,
            progress=progress,
            baas_status=baas_status,
            baas_publish_id=baas_publish_id,
            bot_id=publish_record.source_bot_id,
        )

        if stage == PublishStage.ONLINE and current_status == PublishStatus.ONLINE_PUB.value:
            owner_id = self._get_owner_id(publish_record)
            bot = self._bot_service.get_bot(
                bot_id=publish_record.source_bot_id,
                user_id=owner_id,
            )
            if not bot:
                raise PublishFlowServiceError(
                    f"Bot不存在: {publish_record.source_bot_id}"
                )

            if not self._provider_behavior(bot).destroys_verify_bot_on_online:
                logger.info(
                    "[PublishFlowService._handle_sync_success] "
                    "Skip destroying verify BaaS bot for this provider: "
                    f"publish_id={publish_id}, bot_id={publish_record.source_bot_id}"
                )
            else:
                # Step 2: 销毁验证 BaaS 层的 bot
                try:
                    self._destroy_bot_by_stage(publish_record, PublishStage.VERIFY)
                    logger.info(
                        f"[PublishFlowService.destroy_publish_history] "
                        f"Bot destroyed: publish_id={publish_id}, stage={PublishStage.VERIFY.value}"
                    )

                except Exception as e:
                    logger.warning(
                        f"[PublishFlowService.destroy_publish_history]"
                        f"Failed to destroy BaaS bots: publish_id={publish_id}, stage={PublishStage.VERIFY.value}, error={e}"
                    )
                    # 销毁 bot 失败不阻塞整体流程

        return PublishFlowResult(
            publish_id=publish_id,
            status=target_status,
            message=f"发布进度同步成功，状态: {baas_status}",
            data=progress,
        )

    def _handle_sync_failure(
        self,
        publish_id: int,
        current_status: PublishStatus,
        ext: dict,
        progress: dict,
        error_message: str | None = None,
    ) -> PublishFlowResult:
        """处理 BaaS 发布失败。

        Args:
            publish_id: 发布单 ID
            current_status: 当前状态
            ext: 扩展字段
            progress: BaaS 发布进度信息
            error_message: 自定义错误信息，未提供时根据失败设备数量生成

        Returns:
            PublishFlowResult: 同步结果
        """
        if error_message is None:
            failed_devices = progress.get("failed_devices", [])
            error_message = f"BaaS 发布失败: {len(failed_devices)} 个设备失败"

        self._clear_retry_flag(ext)
        ext["error_message"] = error_message
        ext["source_status"] = current_status.value
        self._update_publish_status(
            publish_id=publish_id,
            target_status=PublishStatus.FAILED,
            source_status=current_status,
            ext=ext,
        )

        logger.error(f"[PublishFlowService._handle_sync_failure] {error_message}")

        return PublishFlowResult(
            publish_id=publish_id,
            status=PublishStatus.FAILED,
            message=error_message,
            data=progress,
        )

    def sync_publish_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """同步 BaaS 层发布进度并推进 AgentClaw 层发布单状态。

        根据 BaaS 层发布单状态推进 AgentClaw 发布单状态。
        当 BaaS 层状态为 ACTIVE/SUCCESS 时，更新 device_binding 状态和 device_props。

        stage 根据发布单状态自动确定：
        - VALIDATE_PUB -> verify
        - ONLINE_PUB -> release

        Args:
            publish_id: 发布单 ID

        Returns:
            PublishFlowResult: 同步结果
        """
        logger.info(f"[PublishFlowService.sync_publish_progress] Syncing: publish_id={publish_id}")

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        ext = publish_record.ext or {}

        # 如果是重试发布单（retry=True），且 source_status 是 VALIDATE_PUB 或 ONLINE_PUB，则直接返回重启进度

        if ext.get("retry"):
            source_status = ext.get("source_status")
            if source_status in (PublishStatus.VALIDATE_PUB.value, PublishStatus.ONLINE_PUB.value):
                logger.info(f"[PublishFlowService.sync_publish_progress] Detected retry flag with source_status={source_status}, redirecting to sync_restart_progress: publish_id={publish_id}")
                return self.sync_restart_progress(publish_id)

        current_status = PublishStatus(publish_record.status)

        # 如果是失败状态，则直接返回发布失败
        if current_status == PublishStatus.FAILED:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} ，请重试！",
            )

        # 如果是building与built等状态，直接返回
        if current_status in [PublishStatus.BUILDING, PublishStatus.BUILT]:
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} ，请等待！",
            )

        # Step 2: 根据当前状态确定 stage
        stage = self._determine_sync_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.sync_publish_progress] "
                f"Invalid status for sync: {current_status}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} 不支持同步进度",
            )

        # Step 3: 获取 BaaS 层发布单 ID
        publish_info = ext.get("publish", {})
        baas_publish_id = publish_info.get(stage.value)
        if not baas_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_publish_progress] "
                f"No baas_publish_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"未找到 {stage.value} 阶段的 BaaS 发布单 ID",
            )

        # Step 4: 调用 BaaS 层获取发布进度
        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=baas_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_publish_progress] Failed to get progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"获取 BaaS 发布进度失败: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_publish_progress] "
            f"BaaS status: {baas_status}, publish_id={publish_id}"
        )

        # Step 5: 根据 BaaS 状态分发处理
        if baas_status == "SUCCESS":
            return self._handle_sync_success(
                publish_id=publish_id,
                publish_record=publish_record,
                stage=stage,
                current_status=current_status,
                ext=ext,
                baas_publish_id=baas_publish_id,
                progress=progress,
            )

        elif baas_status == "FAILED":
            return self._handle_sync_failure(
                publish_id=publish_id,
                current_status=current_status,
                ext=ext,
                progress=progress,
            )

        else:
            # 其他状态（INIT, PENDING, APPROVING 等）
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"BaaS 发布状态: {baas_status}",
                data=progress,
            )

    def sync_scale_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """查询扩容发布单状态。

        从发布单 ext.scale.publish_id 获取 BaaS 层扩容发布单 ID，
        调用 BaaS 层获取发布进度并返回。

        Args:
            publish_id: 发布单 ID

        Returns:
            PublishFlowResult: 扩容进度结果
        """
        logger.info(f"[PublishFlowService.sync_scale_progress] Syncing scale progress: publish_id={publish_id}")

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)
        ext = publish_record.ext or {}
        scale_info = ext.get("scale", {})
        scale_publish_id = scale_info.get("publish_id")

        if not scale_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_scale_progress] "
                f"No scale_publish_id found, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message="未找到扩容发布单 ID",
            )

        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=scale_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_scale_progress] Failed to get scale progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"获取 BaaS 扩容发布进度失败: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_scale_progress] "
            f"BaaS scale status: {baas_status}, publish_id={publish_id}"
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=current_status,
            message=f"BaaS 扩容状态: {baas_status}",
            data=progress,
        )

    def sync_restart_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """查询重启发布单状态。

        根据发布单状态确定当前阶段，从 ext 中获取 BaaS 层重启发布单 ID，
        调用 BaaS 层获取发布进度并返回。

        Args:
            publish_id: 发布单 ID

        Returns:
            PublishFlowResult: 重启进度结果
        """
        logger.info(f"[PublishFlowService.sync_restart_progress] Syncing restart progress: publish_id={publish_id}")

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # Step 2: 根据状态确定重启阶段
        stage = self._determine_restart_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.sync_restart_progress] "
                f"Invalid status for restart sync: {current_status}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"当前状态 {current_status} 不支持查询重启进度",
            )

        # Step 3: 从 ext 获取 BaaS 层重启发布单 ID
        ext = publish_record.ext or {}
        restart_info = ext.get("restart", {})
        restart_publish_id = restart_info.get(stage.value)

        if not restart_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_restart_progress] "
                f"No restart_publish_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"未找到 {stage.value} 阶段的重启发布单 ID",
            )

        # Step 4: 调用 BaaS 层获取发布进度
        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=restart_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_restart_progress] Failed to get restart progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"获取 BaaS 重启发布进度失败: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_restart_progress] "
            f"BaaS restart status: {baas_status}, publish_id={publish_id}, stage={stage.value}"
        )

        # Step 5: 根据 BaaS 状态推进发布单状态
        # VALIDATING 和 SUCCESS 是已完成的稳态，不需要推进
        if current_status in (PublishStatus.VALIDATING, PublishStatus.SUCCESS):
            logger.info(
                f"[PublishFlowService.sync_restart_progress] "
                f"Current status is {current_status}, skip status update: publish_id={publish_id}"
            )

            # 如果失败，刚需要把当前发布单状态更新为失败
            if baas_status == "FAILED":
                return self._handle_sync_failure(
                    publish_id=publish_id,
                    current_status=current_status,
                    ext=ext,
                    progress=progress,
                    error_message=f"重启发布状态: {baas_status}",
                )

            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"重启发布状态: {baas_status}",
                action="sync_restart",
                data=progress,
            )

        if baas_status == "SUCCESS":
            return self._handle_sync_success(
                publish_id=publish_id,
                publish_record=publish_record,
                stage=stage,
                current_status=current_status,
                ext=ext,
                baas_publish_id=restart_publish_id,
                progress=progress,
            )

        elif baas_status == "FAILED":
            return self._handle_sync_failure(
                publish_id=publish_id,
                current_status=current_status,
                ext=ext,
                progress=progress,
            )

        else:
            # 其他状态（INIT, PENDING, APPROVING 等）
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"重启发布状态: {baas_status}",
                action="sync_restart",
                data=progress,
            )

    def get_baas_publish_progress(
        self,
        *,
        baas_publish_id: int | str,
        include_devices: bool = True,
    ) -> Dict[str, Any]:
        """查询 BaaS 发布进度。"""
        logger.info(
            f"[PublishFlowService.get_baas_publish_progress] Query BaaS progress: "
            f"baas_publish_id={baas_publish_id}, include_devices={include_devices}"
        )
        try:
            return self._baas_service.get_publish_progress(
                publish_id=int(baas_publish_id),
                include_devices=include_devices,
            )
        except Exception as e:
            logger.error(
                f"[PublishFlowService.get_baas_publish_progress] "
                f"Failed to get BaaS publish progress: baas_publish_id={baas_publish_id}, "
                f"error={e}"
            )
            raise
