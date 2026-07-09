"""Runtime target helpers for cron relay service."""
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


@dataclass(frozen=True)
class CronRuntimeTarget:
    bot_id: str
    bot_name: str
    owner_id: str
    bot_type: str
    runtime_stage: str
    binding_id: int
    publish_id: int | None = None
    publish_status: str | None = None
    device_uuid: str | None = None
    provider_device_id: str | None = None
    bot_uuid: str | None = None
    instance_status: str | None = None
    instance_health: str | None = None
    instance_health_status: str | None = None


class CronRuntimeTargetMixin:
    @staticmethod
    def _read_field(value: Any, field: str) -> Any:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    def _expand_runtime_targets(
        self,
        targets: list[CronRuntimeTarget],
    ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
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
        if not isinstance(device_provider, str):
            return [target], []
        if device_provider not in MULTI_INSTANCE_DEVICE_PROVIDERS:
            return [target], []

        try:
            instance_result = self._device_provider.get_instances(
                binding_id=target.binding_id,
                health_check=False,
            )
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

        bot_uuid = instance_result.get("bot_uuid")
        devices = instance_result.get("devices", []) or []
        if not devices:
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
        failed_targets: list[dict[str, Any]] = []
        for instance in devices:
            device_uuid = self._read_field(instance, "device_uuid")
            if not device_uuid:
                failed_targets.append(
                    self._failed_target_from_values(
                        bot_id=target.bot_id,
                        bot_name=target.bot_name,
                        owner_id=target.owner_id,
                        runtime_stage=target.runtime_stage,
                        publish_id=target.publish_id,
                        reason="instance_device_uuid_missing",
                        message=(
                            f"Bot {target.bot_id} stage={target.runtime_stage} "
                            "instance has no device_uuid"
                        ),
                        provider_device_id=self._read_field(
                            instance, "provider_device_id"
                        ),
                        bot_uuid=self._read_field(instance, "bot_uuid") or bot_uuid,
                        instance_status=self._read_field(instance, "status"),
                        instance_health=self._read_field(instance, "health"),
                        instance_health_status=self._read_field(
                            instance, "health_status"
                        ),
                    )
                )
                continue

            expanded.append(
                replace(
                    target,
                    device_uuid=str(device_uuid),
                    provider_device_id=self._read_field(
                        instance, "provider_device_id"
                    ),
                    bot_uuid=self._read_field(instance, "bot_uuid") or bot_uuid,
                    instance_status=self._read_field(instance, "status"),
                    instance_health=self._read_field(instance, "health"),
                    instance_health_status=self._read_field(
                        instance, "health_status"
                    ),
                )
            )

        return expanded, failed_targets

    def _decorate_runtime_item(
        self,
        item: dict[str, Any],
        target: CronRuntimeTarget,
    ) -> None:
        item["bot_id"] = target.bot_id
        item["bot_name"] = target.bot_name
        item["owner_id"] = target.owner_id
        item["runtime_stage"] = target.runtime_stage
        if target.publish_id is not None:
            item["publish_id"] = target.publish_id
        if target.publish_status is not None:
            item["publish_status"] = target.publish_status
        if target.device_uuid:
            item["device_uuid"] = target.device_uuid
        if target.provider_device_id:
            item["provider_device_id"] = target.provider_device_id
        if target.bot_uuid:
            item["bot_uuid"] = target.bot_uuid
        if target.instance_status is not None:
            item["instance_status"] = target.instance_status
        if target.instance_health is not None:
            item["instance_health"] = target.instance_health
        if target.instance_health_status is not None:
            item["instance_health_status"] = target.instance_health_status

    def _resolve_runtime_context(self, target: CronRuntimeTarget):
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
        return self._failed_target_from_values(
            bot_id=target.bot_id,
            bot_name=target.bot_name,
            owner_id=target.owner_id,
            runtime_stage=target.runtime_stage,
            publish_id=target.publish_id,
            reason=reason,
            message=message,
            device_uuid=target.device_uuid,
            provider_device_id=target.provider_device_id,
            bot_uuid=target.bot_uuid,
            instance_status=target.instance_status,
            instance_health=target.instance_health,
            instance_health_status=target.instance_health_status,
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
        provider_device_id: str | None = None,
        bot_uuid: str | None = None,
        instance_status: str | None = None,
        instance_health: str | None = None,
        instance_health_status: str | None = None,
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
        if provider_device_id:
            failed_target["provider_device_id"] = provider_device_id
        if bot_uuid:
            failed_target["bot_uuid"] = bot_uuid
        if instance_status is not None:
            failed_target["instance_status"] = instance_status
        if instance_health is not None:
            failed_target["instance_health"] = instance_health
        if instance_health_status is not None:
            failed_target["instance_health_status"] = instance_health_status
        return failed_target

    def _should_fan_out_runtime_operation(
        self,
        target: CronRuntimeTarget,
        *,
        method: str,
        path: str,
    ) -> bool:
        if target.runtime_stage == RUNTIME_STAGE_DRAFT:
            return False
        if target.bot_type != "service":
            return False
        method_upper = method.upper()
        return method_upper == "PUT" or (
            method_upper == "POST" and path.rstrip("/").endswith("/run")
        )

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
        item: dict[str, Any] = {}
        self._decorate_runtime_item(item, target)
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
                if isinstance(data, dict):
                    self._decorate_runtime_item(data, target)
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

        return {
            "success": succeeded > 0,
            "message": "OK" if succeeded > 0 else "all runtime instances failed",
            "data": {
                "results": response_items,
                "succeeded": succeeded,
                "failed": len(failed),
            },
            "failed_targets": failed,
        }
