"""Cron 运行态操作。

每个入口明确处理草稿态、发布态和多实例行为，再复用目标解析与转发能力。
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
    """定时任务运行态接口的用例入口。

    草稿态始终按单 binding 转发；发布态根据 provider 选择单目标转发或
    多实例 fan-out，并保持对应接口的响应结构。
    """

    # ── 目标与设备校验 ────────────────────────────────────────────

    def _validate_runtime_stage(self, runtime_stage: str) -> None:
        """校验 runtime_stage 查询参数。"""
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
        """解析一次请求要访问的运行态目标。"""
        self._validate_runtime_stage(runtime_stage)
        bot = self._bot_provider.get_bot(bot_id, user_id)

        # verify/online 从对应发布记录中取得 binding 和发布元数据。
        if runtime_stage != RUNTIME_STAGE_DRAFT:
            return bot, self._resolve_published_runtime_target(
                bot,
                user_id,
                runtime_stage,
            )

        # draft 使用 Bot 当前绑定，不附带发布信息。
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
        """读取运行态 binding 对应的设备记录。"""
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
        """确保运行态设备处于可转发状态。"""
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
        """读取并校验运行态设备。"""
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
        """校验目标 binding 可用于 adapter 转发。"""
        self._get_active_runtime_device(
            bot_id=bot_id,
            runtime_stage=runtime_stage,
            target=target,
        )

    def _runtime_instances_unavailable_response(
        self,
        failed_targets: list[dict[str, Any]],
    ) -> dict:
        """生成发布态实例不可用的统一响应。"""
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

    # ── 单目标与多实例转发 ────────────────────────────────────────

    def _decorate_single_result(
        self,
        result: dict,
        *,
        target: CronRuntimeTarget,
        bot: dict,
        include_runtime: bool,
    ) -> None:
        """为单目标 adapter 响应补充 bot 与运行态元数据。"""
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
        """转发到单个运行态目标。"""
        # 单目标操作依次完成目标解析、设备校验、转发和返回值装饰。
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
        """发布态多实例 fan-out；单实例 provider 保持单次转发。"""
        # BaaS/Teclaw 会得到多个带 device_uuid 的目标；Arca/local 保持一个目标。
        expanded_targets, failed_targets = await self._expand_runtime_target(
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

        # 单实例 provider 沿用普通响应结构，不包装 results 数组。
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

    # ── 运行态接口 ────────────────────────────────────────────────

    async def get_cron_status(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
    ) -> dict:
        """获取草稿态 cron 能力状态。"""
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
        """获取指定运行态的任务详情。"""
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
        """在草稿态创建定时任务。"""
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
        """更新定时任务；发布态仅支持启停同步。"""
        # 草稿态允许完整更新，并且只转发到当前草稿 binding。
        if runtime_stage == RUNTIME_STAGE_DRAFT:
            return await self._forward_single_stage_request(
                bot_id=bot_id,
                user_id=user_id,
                runtime_stage=runtime_stage,
                method="PUT",
                path=f"/api/cron/{task_id}",
                body=body,
            )

        # 发布态只接受 enabled，并同步到该 stage 的所有运行实例。
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
        """删除草稿态定时任务。"""
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
        """触发定时任务；发布态会按运行实例 fan-out。"""
        # 草稿态执行一次；发布态对同一任务逐实例触发。
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
        """获取任务运行记录；发布态可按实例筛选。"""
        if device_uuid and runtime_stage == RUNTIME_STAGE_DRAFT:
            raise CronRelayError(
                "device_uuid requires published runtime_stage",
                error_code=400,
            )

        path = f"/api/cron/{task_id}/runs"
        params = {"limit": limit}

        # 草稿态只有一个目标，直接返回 adapter 的原始 runs 结构。
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

        # 发布态先展开运行实例，再决定定向查询或聚合全部实例。
        expanded_targets, failed_targets = await self._expand_runtime_target(
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

        # 未指定实例时，多实例结果按 results[] 聚合并保留每个 device_uuid。
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

        # Arca/local 等单实例 provider 保持与草稿态一致的 runs 响应结构。
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
