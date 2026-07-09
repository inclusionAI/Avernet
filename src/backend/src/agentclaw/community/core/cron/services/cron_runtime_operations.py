"""Explicit cron runtime operations.

This module keeps HTTP endpoint intent out of generic relay forwarding. Routers
call these use-case methods; the methods decide stage and instance behavior.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.protocols import DeviceBindingStatus
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
    RUNTIME_STAGE_DRAFT,
    VALID_RUNTIME_STAGES,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class CronRuntimeOperationsMixin:
    def _validate_runtime_stage(self, runtime_stage: str) -> None:
        if runtime_stage not in VALID_RUNTIME_STAGES:
            raise CronRelayError(
                f"Invalid runtime_stage: {runtime_stage}",
                error_code=400,
            )

    def _resolve_request_target(
        self,
        *,
        bot_id: str,
        user_id: str,
        runtime_stage: str,
    ) -> tuple[dict, CronRuntimeTarget]:
        self._validate_runtime_stage(runtime_stage)
        bot = self._bot_provider.get_bot(bot_id, user_id)
        if runtime_stage != RUNTIME_STAGE_DRAFT:
            return bot, self._resolve_published_runtime_target(
                bot,
                user_id,
                runtime_stage,
            )

        binding_id = bot.get("binding_id")
        if not binding_id:
            raise ValueError(f"Bot {bot_id} has no device binding")

        return bot, CronRuntimeTarget(
            bot_id=bot_id,
            bot_name=bot.get("bot_name", ""),
            owner_id=bot.get("owner_id") or user_id,
            bot_type=bot.get("bot_type") or "personal",
            runtime_stage=RUNTIME_STAGE_DRAFT,
            binding_id=binding_id,
        )

    def _read_runtime_device(
        self,
        *,
        bot_id: str,
        target: CronRuntimeTarget,
    ) -> Any:
        try:
            return self._device_provider.get_device(binding_id=target.binding_id)
        except Exception as e:
            logger.error(
                "[_read_runtime_device] Device lookup failed for "
                "bot %s: %s",
                bot_id,
                e,
            )
            if isinstance(e, CronRelayError):
                raise
            raise ValueError(f"Device not available: {e}")

    def _ensure_runtime_device_active(
        self,
        *,
        bot_id: str,
        runtime_stage: str,
        device: Any,
    ) -> None:
        device_status = self._read_field(device, "status")
        if device_status == DeviceBindingStatus.ACTIVE:
            return

        error = CronRelayError(
            f"Bot {bot_id} runtime_stage={runtime_stage} "
            f"device not ACTIVE (status={device_status})",
            error_code=409,
        )
        logger.error(
            "[_ensure_runtime_device_active] Device status check failed for "
            "bot %s: %s",
            bot_id,
            error,
        )
        raise error

    def _get_active_runtime_device(
        self,
        *,
        bot_id: str,
        runtime_stage: str,
        target: CronRuntimeTarget,
    ) -> Any:
        device = self._read_runtime_device(bot_id=bot_id, target=target)
        self._ensure_runtime_device_active(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            device=device,
        )
        return device

    def _ensure_active_runtime_device(
        self,
        *,
        bot_id: str,
        runtime_stage: str,
        target: CronRuntimeTarget,
    ) -> None:
        self._get_active_runtime_device(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            target=target,
        )

    def _runtime_instances_unavailable_response(
        self,
        failed_targets: list[dict[str, Any]],
    ) -> dict:
        return {
            "success": False,
            "message": "runtime instances unavailable",
            "data": {
                "results": [],
                "succeeded": 0,
                "failed": len(failed_targets),
            },
            "failed_targets": failed_targets,
        }

    def _decorate_single_result(
        self,
        result: dict,
        *,
        target: CronRuntimeTarget,
        bot: dict,
        include_runtime: bool,
    ) -> None:
        if not (
            result.get("success")
            and result.get("data")
            and isinstance(result["data"], dict)
        ):
            return

        result["data"]["bot_id"] = target.bot_id
        result["data"]["bot_name"] = bot.get("bot_name", "")
        result["data"]["owner_id"] = target.owner_id
        if include_runtime:
            result["data"]["runtime_stage"] = target.runtime_stage
            if target.publish_id is not None:
                result["data"]["publish_id"] = target.publish_id
            if target.publish_status is not None:
                result["data"]["publish_status"] = target.publish_status

    async def _forward_single_stage_request(
        self,
        *,
        bot_id: str,
        user_id: str,
        runtime_stage: str,
        method: str,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        bot, target = self._resolve_request_target(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=runtime_stage,
        )
        self._ensure_active_runtime_device(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            target=target,
        )
        result = await self._forward_runtime_target_request(
            target,
            method=method,
            path=path,
            body=body,
            params=params,
        )
        self._decorate_single_result(
            result,
            target=target,
            bot=bot,
            include_runtime=runtime_stage != RUNTIME_STAGE_DRAFT,
        )
        return result

    async def _forward_all_runtime_instances_or_single(
        self,
        *,
        target: CronRuntimeTarget,
        bot: dict,
        device: Any,
        method: str,
        path: str,
        body: Optional[dict],
        params: Optional[dict],
    ) -> dict:
        expanded_targets, failed_targets = self._expand_runtime_target(
            target,
            device=device,
        )
        if any(expanded.device_uuid for expanded in expanded_targets):
            return await self._forward_multi_instance_request(
                expanded_targets,
                method=method,
                path=path,
                body=body,
                params=params,
                failed_targets=failed_targets,
            )
        if failed_targets and not expanded_targets:
            return self._runtime_instances_unavailable_response(failed_targets)

        single_target = expanded_targets[0] if expanded_targets else target
        result = await self._forward_runtime_target_request(
            single_target,
            method=method,
            path=path,
            body=body,
            params=params,
        )
        self._decorate_single_result(
            result,
            target=single_target,
            bot=bot,
            include_runtime=True,
        )
        return result

    async def get_cron_status(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
    ) -> dict:
        return await self._forward_single_stage_request(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=RUNTIME_STAGE_DRAFT,
            method="GET",
            path="/api/cron/status",
        )

    async def get_cron_detail(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        task_id: str,
        runtime_stage: str = RUNTIME_STAGE_DRAFT,
    ) -> dict:
        return await self._forward_single_stage_request(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=runtime_stage,
            method="GET",
            path=f"/api/cron/{task_id}",
        )

    async def create_cron(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        body: dict,
    ) -> dict:
        return await self._forward_single_stage_request(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=RUNTIME_STAGE_DRAFT,
            method="POST",
            path="/api/cron",
            body=body,
        )

    async def update_cron(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        task_id: str,
        body: Optional[dict],
        runtime_stage: str = RUNTIME_STAGE_DRAFT,
    ) -> dict:
        if runtime_stage == RUNTIME_STAGE_DRAFT:
            return await self._forward_single_stage_request(
                bot_id=bot_id,
                user_id=user_id,
                runtime_stage=runtime_stage,
                method="PUT",
                path=f"/api/cron/{task_id}",
                body=body,
            )

        if body is not None and not isinstance(body, dict):
            raise CronRelayError("body must be a dictionary", error_code=400)

        body = body or {}
        if set(body.keys()) != {"enabled"}:
            raise CronRelayError("发布态定时任务不支持编辑，仅允许启停", error_code=403)
        if not isinstance(body.get("enabled"), bool):
            raise CronRelayError("enabled must be bool", error_code=400)

        bot, target = self._resolve_request_target(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=runtime_stage,
        )
        device = self._get_active_runtime_device(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            target=target,
        )
        return await self._forward_all_runtime_instances_or_single(
            target=target,
            bot=bot,
            device=device,
            method="PUT",
            path=f"/api/cron/{task_id}",
            body=body,
            params=None,
        )

    async def delete_cron(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        task_id: str,
        runtime_stage: str = RUNTIME_STAGE_DRAFT,
    ) -> dict:
        self._validate_runtime_stage(runtime_stage)
        if runtime_stage != RUNTIME_STAGE_DRAFT:
            raise CronRelayError("发布态定时任务不支持删除", error_code=403)
        return await self._forward_single_stage_request(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=runtime_stage,
            method="DELETE",
            path=f"/api/cron/{task_id}",
        )

    async def run_cron(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        task_id: str,
        force: bool = False,
        runtime_stage: str = RUNTIME_STAGE_DRAFT,
    ) -> dict:
        if runtime_stage == RUNTIME_STAGE_DRAFT:
            return await self._forward_single_stage_request(
                bot_id=bot_id,
                user_id=user_id,
                runtime_stage=runtime_stage,
                method="POST",
                path=f"/api/cron/{task_id}/run",
                params={"force": force},
            )

        bot, target = self._resolve_request_target(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=runtime_stage,
        )
        device = self._get_active_runtime_device(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            target=target,
        )
        return await self._forward_all_runtime_instances_or_single(
            target=target,
            bot=bot,
            device=device,
            method="POST",
            path=f"/api/cron/{task_id}/run",
            body=None,
            params={"force": force},
        )

    async def get_cron_runs(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        task_id: str,
        limit: int = 20,
        runtime_stage: str = RUNTIME_STAGE_DRAFT,
        device_uuid: str | None = None,
    ) -> dict:
        if device_uuid and runtime_stage == RUNTIME_STAGE_DRAFT:
            raise CronRelayError(
                "device_uuid requires published runtime_stage",
                error_code=400,
            )

        path = f"/api/cron/{task_id}/runs"
        params = {"limit": limit}
        if runtime_stage == RUNTIME_STAGE_DRAFT:
            return await self._forward_single_stage_request(
                bot_id=bot_id,
                user_id=user_id,
                runtime_stage=runtime_stage,
                method="GET",
                path=path,
                params=params,
            )

        bot, target = self._resolve_request_target(
            bot_id=bot_id,
            user_id=user_id,
            runtime_stage=runtime_stage,
        )
        device = self._get_active_runtime_device(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            target=target,
        )

        expanded_targets, failed_targets = self._expand_runtime_target(
            target,
            device=device,
        )
        if device_uuid:
            matched_targets = [
                expanded
                for expanded in expanded_targets
                if expanded.device_uuid == device_uuid
            ]
            if matched_targets:
                routed_target = matched_targets[0]
                result = await self._forward_runtime_target_request(
                    routed_target,
                    method="GET",
                    path=path,
                    body=None,
                    params=params,
                )
                if result.get("success") and isinstance(result.get("data"), dict):
                    self._decorate_runtime_item(result["data"], routed_target)
                return result
            if expanded_targets:
                raise CronRelayError(
                    (
                        f"device_uuid={device_uuid} not found for "
                        f"runtime_stage={runtime_stage}"
                    ),
                    error_code=404,
                )
            return self._runtime_instances_unavailable_response(failed_targets)

        if any(expanded.device_uuid for expanded in expanded_targets):
            return await self._forward_multi_instance_request(
                expanded_targets,
                method="GET",
                path=path,
                body=None,
                params=params,
                failed_targets=failed_targets,
            )
        if failed_targets and not expanded_targets:
            return self._runtime_instances_unavailable_response(failed_targets)

        single_target = expanded_targets[0] if expanded_targets else target
        result = await self._forward_runtime_target_request(
            single_target,
            method="GET",
            path=path,
            body=None,
            params=params,
        )
        self._decorate_single_result(
            result,
            target=single_target,
            bot=bot,
            include_runtime=True,
        )
        return result
