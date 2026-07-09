"""Cron 运行态目标构造、实例展开与结果装饰。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Optional

from agentclaw.community.core.cron.protocols import DeviceBindingStatus
from agentclaw.community.log import get_logger

logger = get_logger()


RUNTIME_STAGE_DRAFT = "draft"
RUNTIME_STAGE_VERIFY = "verify"
RUNTIME_STAGE_ONLINE = "online"
VALID_RUNTIME_STAGES = {
    RUNTIME_STAGE_DRAFT,
    RUNTIME_STAGE_VERIFY,
    RUNTIME_STAGE_ONLINE,
}
MULTI_INSTANCE_DEVICE_PROVIDERS = {"baas", "teclaw"}
SINGLE_INSTANCE_DEVICE_PROVIDERS = {"arca", "local"}


@dataclass(frozen=True)
class CronRuntimeTarget:
    """一次 cron 请求的运行态路由目标。

    ``device_uuid`` 为空表示按 binding 路由；有值表示按发布态实例路由。
    """

    bot_id: str
    bot_name: str
    owner_id: str
    bot_type: str
    runtime_stage: str
    binding_id: int
    publish_id: int | None = None
    publish_status: str | None = None
    device_uuid: str | None = None


class CronRuntimeTargetMixin:
    @staticmethod
    def _read_field(value: Any, field: str) -> Any:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    def _get_runtime_devices(self, target: CronRuntimeTarget) -> list[str]:
        """获取发布态 fan-out 所需的运行设备 UUID 列表。"""
        return self._device_provider.list_devices_by_runtime_binding(
            binding_id=target.binding_id,
        )

    def _expand_runtime_targets(
        self,
        targets: list[CronRuntimeTarget],
    ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
        """将一组运行态目标展开成实际请求目标。"""
        expanded: list[CronRuntimeTarget] = []
        failed_targets: list[dict[str, Any]] = []
        for target in targets:
            target_expanded, target_failed = self._expand_runtime_target(target)
            expanded.extend(target_expanded)
            failed_targets.extend(target_failed)
        return expanded, failed_targets

    def _expand_runtime_target(
        self,
        target: CronRuntimeTarget,
        *,
        device: Any | None = None,
    ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
        """将服务 bot 多实例发布态目标展开到 device_uuid 维度。"""
        if target.bot_type != "service" or target.device_uuid:
            return [target], []

        try:
            if device is None:
                device = self._device_provider.get_device(
                    binding_id=target.binding_id
                )
        except Exception as e:
            return [], [self._failed_target(target, "device_unavailable", str(e))]

        device_status = self._read_field(device, "status")
        if device_status != DeviceBindingStatus.ACTIVE:
            return [], [
                self._failed_target(
                    target,
                    "binding_not_active",
                    (
                        f"Bot {target.bot_id} stage={target.runtime_stage} "
                        f"device not ACTIVE (status={device_status})"
                    ),
                )
            ]

        device_provider = self._read_field(device, "device_provider")
        if isinstance(device_provider, str):
            device_provider = device_provider.lower()
        # arca/local 只有一个运行设备，按 binding 转发一次即可。
        if device_provider in SINGLE_INSTANCE_DEVICE_PROVIDERS:
            return [target], []
        if device_provider not in MULTI_INSTANCE_DEVICE_PROVIDERS:
            return [], [
                self._failed_target(
                    target,
                    "unsupported_device_provider",
                    (
                        f"Bot {target.bot_id} stage={target.runtime_stage} "
                        f"has unsupported device_provider={device_provider}"
                    ),
                )
            ]

        # baas/teclaw 发布态可能存在多个运行设备，需要逐实例转发。
        try:
            device_uuids = self._get_runtime_devices(target)
        except Exception as e:
            logger.error(
                "[_expand_runtime_target] Failed to query instances for "
                "bot=%s stage=%s binding=%s: %s",
                target.bot_id,
                target.runtime_stage,
                target.binding_id,
                e,
            )
            return [], [self._failed_target(target, "instances_query_failed", str(e))]

        if not device_uuids:
            return [], [
                self._failed_target(
                    target,
                    "instances_not_found",
                    (
                        f"Bot {target.bot_id} stage={target.runtime_stage} "
                        f"has no runtime instances"
                    ),
                )
            ]

        expanded: list[CronRuntimeTarget] = []
        for device_uuid in device_uuids:
            expanded.append(
                replace(
                    target,
                    device_uuid=str(device_uuid),
                )
            )

        return expanded, []

    def _decorate_runtime_item(
        self,
        item: dict[str, Any],
        target: CronRuntimeTarget,
    ) -> None:
        """为 adapter 返回项补充 cron relay 的运行态元数据。"""
        item["bot_id"] = target.bot_id
        item["bot_name"] = target.bot_name
        item["owner_id"] = target.owner_id
        if target.bot_type != "service":
            return

        item["runtime_stage"] = target.runtime_stage
        if target.publish_id is not None:
            item["publish_id"] = target.publish_id
        if target.publish_status is not None:
            item["publish_status"] = target.publish_status
        if target.device_uuid:
            item["device_uuid"] = target.device_uuid

    def _resolve_runtime_context(self, target: CronRuntimeTarget):
        """根据 target 构造 adapter 连接上下文。"""
        kwargs: dict[str, Any] = {}
        if target.device_uuid:
            kwargs["device_uuid"] = target.device_uuid
        if target.runtime_stage == RUNTIME_STAGE_DRAFT:
            return self._resolver.resolve_for_bot(
                target.bot_id,
                target.owner_id,
                **kwargs,
            )
        return self._resolver.resolve_for_binding(
            target.binding_id,
            target.owner_id,
            bot_id=target.bot_id,
            **kwargs,
        )

    async def _fetch_runtime_target_crons(
        self,
        target: CronRuntimeTarget,
        user_id: str,
        path: str = "/api/cron",
    ) -> dict:
        """读取单个运行态目标的 cron 列表或运行中任务。"""
        try:
            device = self._device_provider.get_device(binding_id=target.binding_id)
            device_status = self._read_field(device, "status")
            if device_status != DeviceBindingStatus.ACTIVE:
                return {
                    "success": False,
                    "reason": "binding_not_active",
                    "error": (
                        f"Bot {target.bot_id} stage={target.runtime_stage} "
                        f"device not ACTIVE (status={device_status})"
                    ),
                }
        except Exception as e:
            return {"success": False, "reason": "device_unavailable", "error": str(e)}

        try:
            ctx = self._resolve_runtime_context(target)
        except Exception as e:
            return {"success": False, "reason": "resolver_failed", "error": str(e)}

        try:
            result = await self._transport.invoke(ctx.conn_info, "GET", path)
        except Exception as e:
            logger.error(
                "[_fetch_runtime_target_crons] Adapter request failed for "
                "bot=%s stage=%s: %s",
                target.bot_id,
                target.runtime_stage,
                e,
            )
            return {"success": False, "reason": "cron_api_failed", "error": str(e)}

        if not result.get("success", True):
            return {
                "success": False,
                "reason": "cron_api_failed",
                "error": result.get("message") or result.get("error") or "cron api failed",
            }

        return result

    def _failed_target(
        self,
        target: CronRuntimeTarget,
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        """按运行态目标生成失败项。"""
        return self._failed_target_from_values(
            bot_id=target.bot_id,
            bot_name=target.bot_name,
            owner_id=target.owner_id,
            runtime_stage=target.runtime_stage,
            publish_id=target.publish_id,
            reason=reason,
            message=message,
            device_uuid=target.device_uuid,
        )

    def _failed_target_from_values(
        self,
        *,
        bot_id: str,
        bot_name: str,
        owner_id: str,
        runtime_stage: str,
        publish_id: int | None,
        reason: str,
        message: str,
        device_uuid: str | None = None,
    ) -> dict[str, Any]:
        failed_target: dict[str, Any] = {
            "bot_id": bot_id,
            "bot_name": bot_name,
            "owner_id": owner_id,
            "runtime_stage": runtime_stage,
            "reason": reason,
            "message": message,
        }
        if publish_id is not None:
            failed_target["publish_id"] = publish_id
        if device_uuid:
            failed_target["device_uuid"] = device_uuid
        return failed_target

    async def _forward_runtime_target_request(
        self,
        target: CronRuntimeTarget,
        *,
        method: str,
        path: str,
        body: Optional[dict],
        params: Optional[dict],
    ) -> dict:
        ctx = self._resolve_runtime_context(target)
        return await self._transport.invoke(ctx.conn_info, method, path, body, params)

    def _runtime_target_result_item(
        self,
        target: CronRuntimeTarget,
        *,
        success: bool,
        data: Any | None = None,
        reason: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "bot_id": target.bot_id,
            "bot_name": target.bot_name,
            "owner_id": target.owner_id,
        }
        if target.bot_type == "service":
            item["runtime_stage"] = target.runtime_stage
            if target.publish_id is not None:
                item["publish_id"] = target.publish_id
            if target.publish_status is not None:
                item["publish_status"] = target.publish_status
        if target.device_uuid:
            item["device_uuid"] = target.device_uuid
        item["success"] = success
        if data is not None:
            item["data"] = data
        if reason:
            item["reason"] = reason
        if message:
            item["message"] = message
        return item

    async def _forward_multi_instance_request(
        self,
        targets: list[CronRuntimeTarget],
        *,
        method: str,
        path: str,
        body: Optional[dict],
        params: Optional[dict],
        failed_targets: list[dict[str, Any]] | None = None,
    ) -> dict:
        """并发转发到多个运行实例并聚合每个实例的结果。"""
        failed = list(failed_targets or [])
        tasks = [
            self._forward_runtime_target_request(
                target,
                method=method,
                path=path,
                body=body,
                params=params,
            )
            for target in targets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        response_items: list[dict[str, Any]] = []
        succeeded = 0
        for target, result in zip(targets, results):
            if isinstance(result, Exception):
                message = str(result)
                failed.append(self._failed_target(target, "cron_api_failed", message))
                response_items.append(
                    self._runtime_target_result_item(
                        target,
                        success=False,
                        reason="cron_api_failed",
                        message=message,
                    )
                )
                continue

            if result.get("success", True):
                succeeded += 1
                data = result.get("data")
                response_items.append(
                    self._runtime_target_result_item(
                        target,
                        success=True,
                        data=data,
                    )
                )
                continue

            reason = result.get("reason") or "cron_api_failed"
            message = result.get("error") or result.get("message") or "cron api failed"
            failed.append(self._failed_target(target, reason, message))
            response_items.append(
                self._runtime_target_result_item(
                    target,
                    success=False,
                    reason=reason,
                    message=message,
                )
            )

        failed_count = len(failed)
        # 多实例写操作需要所有实例成功；部分成功也返回失败供前端展示明细。
        if succeeded > 0 and failed_count == 0:
            success = True
            message = "OK"
        elif succeeded > 0:
            success = False
            message = "partial runtime instances failed"
        else:
            success = False
            message = "all runtime instances failed"

        return {
            "success": success,
            "message": message,
            "data": {
                "results": response_items,
                "succeeded": succeeded,
                "failed": failed_count,
            },
            "failed_targets": failed,
        }
