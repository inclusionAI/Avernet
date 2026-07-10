"""Cron 运行态目标解析与转发。

- 将 Bot 的 draft/verify/online binding 表示为统一目标。
- 按设备 provider 将目标展开到 binding 或 device_uuid 维度。
- 负责连接解析、读请求超时、失败项和多实例结果聚合。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Optional

from agentclaw.community.core.cron.errors import CronApiTimeoutError
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

# Cron 查询只等待有限时间；并发数用于限制同步设备请求占用的线程数。
CRON_READ_TIMEOUT_SECONDS = 10.0
RUNTIME_DEVICE_QUERY_TIMEOUT_SECONDS = 10.0
RUNTIME_DEVICE_QUERY_CONCURRENCY = 8
RUNTIME_QUERY_PREPARE_CONCURRENCY = 8


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
    """负责运行态目标展开、连接解析、请求转发和实例结果聚合。"""

    @staticmethod
    def _read_field(value: Any, field: str) -> Any:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    # ── 运行实例展开 ──────────────────────────────────────────────

    async def _get_runtime_devices(self, target: CronRuntimeTarget) -> list[str]:
        """获取发布态 fan-out 所需的运行设备 UUID 列表。"""
        # 设备列表接口是同步调用，放入线程后可与其他运行态目标并发执行。
        return await asyncio.wait_for(
            asyncio.to_thread(
                self._device_provider.list_devices_by_runtime_binding,
                binding_id=target.binding_id,
                timeout=RUNTIME_DEVICE_QUERY_TIMEOUT_SECONDS,
            ),
            timeout=RUNTIME_DEVICE_QUERY_TIMEOUT_SECONDS,
        )

    async def _expand_runtime_targets(
        self,
        targets: list[CronRuntimeTarget],
    ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
        """并发将一组运行态目标展开成实际请求目标。"""
        semaphore = asyncio.Semaphore(RUNTIME_DEVICE_QUERY_CONCURRENCY)

        async def expand_one(
            target: CronRuntimeTarget,
        ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
            # 限制同时进行的设备枚举，避免一次查询占满线程池。
            async with semaphore:
                return await self._expand_runtime_target(target)

        results = await asyncio.gather(*(expand_one(target) for target in targets))
        expanded: list[CronRuntimeTarget] = []
        failed_targets: list[dict[str, Any]] = []
        for target_expanded, target_failed in results:
            expanded.extend(target_expanded)
            failed_targets.extend(target_failed)
        return expanded, failed_targets

    async def _expand_runtime_target(
        self,
        target: CronRuntimeTarget,
        *,
        device: Any | None = None,
    ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
        """将目标展开成实际请求目标，并返回无法展开的失败项。

        BaaS/Teclaw 服务 Bot 按 device_uuid 展开；Arca/local、个人 Bot 和
        已指定 device_uuid 的目标保持单目标。
        """
        # 个人 Bot 和已经锁定实例的目标无需再次展开。
        if target.bot_type != "service" or target.device_uuid:
            return [target], []

        # 展开前先确认运行态 binding 可用。
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
            device_uuids = await self._get_runtime_devices(target)
        except TimeoutError:
            return [], [
                self._failed_target(
                    target,
                    "instances_query_timeout",
                    (
                        f"Runtime device query timed out after "
                        f"{RUNTIME_DEVICE_QUERY_TIMEOUT_SECONDS:g}s"
                    ),
                )
            ]
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

        # 每个 UUID 复制成独立目标，后续解析连接时会锁定到对应实例。
        expanded: list[CronRuntimeTarget] = []
        for device_uuid in device_uuids:
            expanded.append(
                replace(
                    target,
                    device_uuid=str(device_uuid),
                )
            )

        return expanded, []

    # ── 连接解析与单目标请求 ──────────────────────────────────────

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

        # 草稿态跟随 Bot 当前 binding；发布态使用发布记录中的 stage binding。
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

    def _prepare_runtime_query(
        self,
        target: CronRuntimeTarget,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        """校验目标设备并构造 cron 查询所需的连接上下文。"""
        try:
            device = self._device_provider.get_device(binding_id=target.binding_id)
            device_status = self._read_field(device, "status")
            if device_status != DeviceBindingStatus.ACTIVE:
                return None, {
                    "success": False,
                    "reason": "binding_not_active",
                    "error": (
                        f"Bot {target.bot_id} stage={target.runtime_stage} "
                        f"device not ACTIVE (status={device_status})"
                    ),
                }
        except Exception as e:
            return None, {
                "success": False,
                "reason": "device_unavailable",
                "error": str(e),
            }

        try:
            return self._resolve_runtime_context(target), None
        except Exception as e:
            return None, {
                "success": False,
                "reason": "resolver_failed",
                "error": str(e),
            }

    async def _prepare_runtime_query_async(
        self,
        target: CronRuntimeTarget,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        """在线程中准备查询上下文，并限制设备控制面并发量。"""
        async with self._runtime_query_prepare_semaphore:
            return await asyncio.to_thread(self._prepare_runtime_query, target)

    async def _fetch_runtime_target_crons(
        self,
        target: CronRuntimeTarget,
        user_id: str,
        path: str = "/api/cron",
    ) -> dict:
        """读取单个运行态目标的 cron 列表或运行中任务。"""
        ctx, failure = await self._prepare_runtime_query_async(target)
        if failure is not None:
            return failure
        assert ctx is not None

        # 下游异常统一转换成可聚合的失败原因。
        try:
            result = await self._invoke_transport(
                ctx.conn_info,
                method="GET",
                path=path,
                body=None,
                params=None,
            )
        except CronApiTimeoutError as e:
            logger.warning(
                "[_fetch_runtime_target_crons] Adapter request timed out for "
                "bot=%s stage=%s: %s",
                target.bot_id,
                target.runtime_stage,
                e,
            )
            return {"success": False, "reason": "cron_api_timeout", "error": str(e)}
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
            runtime_stage=(
                target.runtime_stage if target.bot_type == "service" else None
            ),
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
        runtime_stage: str | None,
        publish_id: int | None,
        reason: str,
        message: str,
        device_uuid: str | None = None,
    ) -> dict[str, Any]:
        failed_target: dict[str, Any] = {
            "bot_id": bot_id,
            "bot_name": bot_name,
            "owner_id": owner_id,
            "reason": reason,
            "message": message,
        }
        if runtime_stage is not None:
            failed_target["runtime_stage"] = runtime_stage
        if publish_id is not None:
            failed_target["publish_id"] = publish_id
        if device_uuid:
            failed_target["device_uuid"] = device_uuid
        return failed_target

    async def _invoke_transport(
        self,
        conn_info: dict[str, Any],
        *,
        method: str,
        path: str,
        body: Optional[dict],
        params: Optional[dict],
    ) -> dict:
        """调用 adapter，并为 cron 读请求设置下游请求超时。"""
        # 列表、详情和 runs 等 GET 使用 cron 读超时；写请求使用 transport 默认值。
        if method.upper() != "GET":
            return await self._transport.invoke(
                conn_info,
                method,
                path,
                body,
                params,
            )

        try:
            return await asyncio.wait_for(
                self._transport.invoke(
                    conn_info,
                    method,
                    path,
                    body,
                    params,
                    timeout=CRON_READ_TIMEOUT_SECONDS,
                ),
                timeout=CRON_READ_TIMEOUT_SECONDS,
            )
        except TimeoutError as e:
            raise CronApiTimeoutError(path, CRON_READ_TIMEOUT_SECONDS) from e

    async def _forward_runtime_target_request(
        self,
        target: CronRuntimeTarget,
        *,
        method: str,
        path: str,
        body: Optional[dict],
        params: Optional[dict],
    ) -> dict:
        """解析目标连接并转发一次 adapter 请求。"""
        ctx = self._resolve_runtime_context(target)
        return await self._invoke_transport(
            ctx.conn_info,
            method=method,
            path=path,
            body=body,
            params=params,
        )

    # ── 失败项与多实例聚合 ────────────────────────────────────────

    def _runtime_target_result_item(
        self,
        target: CronRuntimeTarget,
        *,
        success: bool,
        data: Any | None = None,
        reason: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        """生成包含 Bot、stage 和 device_uuid 的实例结果。"""
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

        # 各实例独立执行，一个实例异常不会取消其他实例。
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

        # 每个实例都保留一条结果，供调用方展示成功、失败及对应 device_uuid。
        response_items: list[dict[str, Any]] = []
        succeeded = 0
        for target, result in zip(targets, results):
            if isinstance(result, Exception):
                message = str(result)
                reason = (
                    "cron_api_timeout"
                    if isinstance(result, CronApiTimeoutError)
                    else "cron_api_failed"
                )
                failed.append(self._failed_target(target, reason, message))
                response_items.append(
                    self._runtime_target_result_item(
                        target,
                        success=False,
                        reason=reason,
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
