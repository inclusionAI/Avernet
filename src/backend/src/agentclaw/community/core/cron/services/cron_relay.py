"""Cron Relay Service — 定时任务中继服务

作为前端和 Bot Adapter 之间的中继层，聚合用户所有 Bots 的定时任务。
不保存任何任务数据，所有操作转发到对应 Bot 的 Adapter。

依赖注入：
- BotInfoProvider: Bot 信息查询
- DeviceConnectionProvider: 设备连接服务

Architecture compliance:
- Depends only on core/cron/protocols.py (Protocol interfaces)
- No dependencies on services/ layer or global singletons
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.repository.template_repository_protocol import (
    TemplateRepository,
)
from agentclaw.community.core.cron.protocols import (
    BotInfoProvider,
    DeviceConnectionProvider,
    DeviceBindingStatus,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
    CronRuntimeTargetMixin,
    RUNTIME_STAGE_DRAFT,
    RUNTIME_STAGE_ONLINE,
    RUNTIME_STAGE_VERIFY,
    VALID_RUNTIME_STAGES,
)
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

logger = get_logger()


class CronRelayService(CronRuntimeTargetMixin):
    """Cron 中继服务 — 转发请求到各 Bot 的 Adapter"""

    @inject
    def __init__(
        self,
        bot_provider: BotService,
        device_provider: DeviceService,
        transport: DeviceAdapterTransport,
        resolver: DeviceContextResolver,
        template_repo: TemplateRepository,
        publish_repo: BotPublishRepositoryProtocol,
    ):
        """Initialise.

        ``BotService`` and ``DeviceService`` structurally satisfy
        ``BotInfoProvider`` and ``DeviceConnectionProvider``
        respectively; we type the params with the concrete classes so
        the injector can resolve them via the bound singletons.

        ``transport`` is the one system boundary (HTTP to the bot's
        engine adapter); prod forwards over httpx, tests/local swap an
        in-memory adapter so the relay runs end-to-end.

        ``resolver`` is the全仓唯一 provider 解析点 — _fetch_bot_crons
        通过 (bot_id, user_id) 拿 typed DeviceContext,替代旧的
        ``device_service.get_device_connection_v2(binding_id, user_id, nick_name)``。
        """
        self._bot_provider: BotInfoProvider = bot_provider
        self._device_provider: DeviceConnectionProvider = device_provider
        self._transport = transport
        self._resolver = resolver
        self._template_repo = template_repo
        self._publish_repo = publish_repo

    async def list_all_crons(
        self,
        user_id: str,
        nick_name: str,
        bot_id: Optional[str] = None
    ) -> dict:
        """获取用户所有 Bots 的定时任务（平铺展示）

        Args:
            user_id: 用户ID
            nick_name: 用户花名
            bot_id: 如果为 "all" 或 None，返回所有 bots 的任务；否则返回指定 bot 的任务

        Returns:
            {"success": True, "data": [...], "total": N}
        """
        # 1. 获取用户的所有 bots
        if bot_id and bot_id != "all":
            bot = self._bot_provider.get_bot(bot_id, user_id)
            bots = [bot]
        else:
            result = self._bot_provider.list_bots_by_owner_or_collaborator(
                user_id,
                page=1,
                page_size=100,
            )
            bots = result.get("items", [])

        if not bots:
            return {"success": True, "data": [], "total": 0}

        # 2. 展开每个 bot 的 runtime target，再并发获取 cron 列表
        targets: list[CronRuntimeTarget] = []
        failed_targets: list[dict[str, Any]] = []
        for bot in bots:
            bot_targets, bot_failed_targets = self._build_runtime_targets(bot, user_id)
            targets.extend(bot_targets)
            failed_targets.extend(bot_failed_targets)
        targets, instance_failed_targets = self._expand_runtime_targets(targets)
        failed_targets.extend(instance_failed_targets)

        tasks = []
        for target in targets:
            task = self._fetch_runtime_target_crons(target, user_id)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 3. 合并结果，添加 bot_id/bot_name/owner_id/runtime_stage
        all_crons = []
        seen_task_ids: set[str] = set()
        success_bots = []
        for target, result in zip(targets, results):
            bot_id_val = target.bot_id

            if isinstance(result, Exception):
                logger.error(
                    "[list_all_crons] Failed to fetch crons for "
                    "bot=%s stage=%s: %s",
                    bot_id_val,
                    target.runtime_stage,
                    result,
                )
                failed_targets.append(
                    self._failed_target(target, "cron_api_failed", str(result))
                )
                continue

            if result.get("success"):
                bot_crons = result.get("data", [])
                added = 0
                for cron in bot_crons:
                    task_id = cron.get("task_id") or cron.get("id")
                    dedupe_key = (
                        f"{target.bot_id}:{target.owner_id}:"
                        f"{target.runtime_stage}:{target.device_uuid or ''}:"
                        f"{task_id}"
                    )
                    if task_id and dedupe_key in seen_task_ids:
                        continue
                    if task_id:
                        seen_task_ids.add(dedupe_key)
                    self._decorate_runtime_item(cron, target)
                    all_crons.append(cron)
                    added += 1
                if added < len(bot_crons):
                    logger.debug(
                        "[list_all_crons] Bot %s stage=%s: deduped %s tasks",
                        bot_id_val,
                        target.runtime_stage,
                        len(bot_crons) - added,
                    )
                if bot_crons:
                    success_bots.append(
                        f"{bot_id_val}:{target.runtime_stage}({added} crons)"
                    )
                else:
                    success_bots.append(
                        f"{bot_id_val}:{target.runtime_stage}(0 crons)"
                    )
            else:
                failed_targets.append(
                    self._failed_target(
                        target,
                        result.get("reason") or "cron_api_failed",
                        result.get("error") or result.get("message") or "cron api failed",
                    )
                )

        if failed_targets:
            logger.warning(
                "[list_all_crons] Partial failure: %s succeeded, %s failed: %s",
                len(success_bots),
                len(failed_targets),
                failed_targets,
            )
        else:
            logger.info(f"[list_all_crons] All {len(success_bots)} bots succeeded")

        return {
            "success": True,
            "data": all_crons,
            "total": len(all_crons),
            "failed_targets": failed_targets,
        }

    async def list_running_crons(
        self,
        user_id: str,
        nick_name: str,
        bot_id: Optional[str] = None
    ) -> dict:
        """获取正在执行的任务列表"""
        # 获取 bot 列表（指定 bot 时只查一个 bot）
        if bot_id and bot_id != "all":
            bots = [self._bot_provider.get_bot(bot_id, user_id)]
        else:
            result = self._bot_provider.list_bots_by_owner_or_collaborator(
                user_id,
                page=1,
                page_size=100,
            )
            bots = result.get("items", [])

        if not bots:
            return {"success": True, "data": [], "failed_targets": []}

        targets: list[CronRuntimeTarget] = []
        failed_targets: list[dict[str, Any]] = []
        for bot in bots:
            bot_targets, bot_failed_targets = self._build_runtime_targets(bot, user_id)
            targets.extend(bot_targets)
            failed_targets.extend(bot_failed_targets)
        targets, instance_failed_targets = self._expand_runtime_targets(targets)
        failed_targets.extend(instance_failed_targets)

        # 并发获取每个 runtime target 的 running 任务
        tasks = []
        for target in targets:
            task = self._fetch_runtime_target_crons(target, user_id, path="/api/cron/running")
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 合并所有 running 任务
        all_running = []
        for target, result in zip(targets, results):
            if isinstance(result, Exception):
                failed_targets.append(
                    self._failed_target(target, "cron_api_failed", str(result))
                )
                continue
            data = result.get("data", {})
            if isinstance(data, dict):
                running_list = data.get("running", [])
                for item in running_list:
                    self._decorate_runtime_item(item, target)
                all_running.extend(running_list)
            elif result.get("success") is False:
                failed_targets.append(
                    self._failed_target(
                        target,
                        result.get("reason") or "cron_api_failed",
                        result.get("error") or result.get("message") or "cron api failed",
                    )
                )

        return {"success": True, "data": all_running, "failed_targets": failed_targets}

    async def find_auto_initiate_and_run(
        self,
        bot_id: str,
        user_id: str,
        nick_name: str,
        force: bool = True,
    ) -> dict:
        """查找 Bot 的 autoInitiate 类型定时任务并触发执行。

        免鉴权场景下由调用方提供 user_id/nick_name，无需登录态。

        Args:
            bot_id: Bot ID
            user_id: 用户ID（调用方传入）
            nick_name: 用户花名（调用方传入，死参，缺省用 user_id）
            force: 是否强制执行，默认 True

        Returns:
            forward_request 的响应

        Raises:
            ValueError: Bot 无设备绑定、设备不在线或未找到 autoInitiate 任务
        """
        # 1. 获取 bot 信息
        bot = self._bot_provider.get_bot(bot_id, user_id)
        binding_id = bot.get("binding_id")
        if not binding_id:
            raise ValueError(f"Bot {bot_id} has no device binding")

        # 2. 检查设备状态
        try:
            device = self._device_provider.get_device(binding_id=binding_id)
            device_status = device.status if hasattr(device, 'status') else device.get("status")
            if device_status != DeviceBindingStatus.ACTIVE:
                raise ValueError(f"Bot {bot_id} device not ACTIVE (status={device_status})")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Device not available: {e}")

        # 3. 列出该 bot 的 cron jobs（通过 DeviceContextResolver + transport）
        try:
            ctx = self._resolver.resolve_for_bot(bot_id, user_id)
            list_result = await self._transport.invoke(ctx.conn_info, "GET", "/api/cron")
        except Exception as e:
            logger.error(f"[find_auto_initiate_and_run] Failed to list crons for bot {bot_id}: {e}")
            raise ValueError(f"Failed to list crons for bot {bot_id}: {e}")

        jobs = list_result.get("data", [])
        if not isinstance(jobs, list):
            jobs = []

        # 4. 过滤出 prompt(message) 中标注 |kind:autoInitiate| 的 job
        auto_initiate_job = None
        for job in jobs:
            payload = job.get("payload", {})
            if not isinstance(payload, dict):
                continue
            message = payload.get("message") or payload.get("prompt") or ""
            if isinstance(message, str) and "kind:autoInitiate" in message:
                auto_initiate_job = job
                break

        if not auto_initiate_job:
            raise ValueError(f"No autoInitiate cron job found for bot {bot_id}")

        task_id = auto_initiate_job.get("id") or auto_initiate_job.get("task_id")
        logger.info(f"[find_auto_initiate_and_run] Found autoInitiate job {task_id} for bot {bot_id}")

        # 5. 复用 forward_request 触发执行
        return await self.forward_request(
            bot_id=bot_id,
            user_id=user_id,
            nick_name=nick_name,
            method="POST",
            path=f"/api/cron/{task_id}/run",
            params={"force": force},
        )

    async def run_single_auto_initiate(
        self,
        bot_id: str,
        user_id: str,
        nick_name: str,
        dima_url: str,
        append_message: str = "",
        model: str | None = None,
    ) -> dict:
        """为单个 DIMA 需求直接发起会话（免鉴权）。

        workflow 从 bot 的 template_config.ext.devflow_workflow 中读取，
        与 cron_auto_setup 保持一致，调用方无需传入。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID（调用方传入）
            nick_name: 用户花名
            dima_url: DIMA 需求 URL
            append_message: 补充说明
            model: 可选模型覆盖

        Returns:
            Engine 的响应

        Raises:
            ValueError: Bot 无设备绑定、设备不在线或无 template_config
        """
        bot = self._bot_provider.get_bot(bot_id, user_id)
        binding_id = bot.get("binding_id")
        if not binding_id:
            raise ValueError(f"Bot {bot_id} has no device binding")

        try:
            device = self._device_provider.get_device(binding_id=binding_id)
            device_status = device.status if hasattr(device, 'status') else device.get("status")
            if device_status != DeviceBindingStatus.ACTIVE:
                raise ValueError(f"Bot {bot_id} device not ACTIVE (status={device_status})")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Device not available: {e}")

        # 从 template_config 中读取 workflow，与 cron_auto_setup 逻辑一致
        workflow_name = ""
        try:
            template = self._template_repo.get_by_bot_id(bot_id)
            if template:
                ext = template.get("ext") or {}
                if isinstance(ext, dict):
                    devflow_workflow = ext.get("devflow_workflow", "")
                    if isinstance(devflow_workflow, dict):
                        workflow_name = devflow_workflow.get("name", "")
                    elif isinstance(devflow_workflow, str):
                        workflow_name = devflow_workflow
        except Exception as e:
            logger.warning(
                "[run_single_auto_initiate] 读取 template_config 失败: %s", e
            )

        body = {
            # Engine renamed this request field dima_url -> work_item_url
            # (OSS-neutral naming). Keep this key in sync with the engine schema.
            "work_item_url": dima_url,
            "user_id": user_id,
            "agent_id": bot.get("bot_id", bot_id),
            "workflow": workflow_name,
            "append_message": append_message,
        }
        if model:
            body["model"] = model

        return await self.forward_request(
            bot_id=bot_id,
            user_id=user_id,
            nick_name=nick_name,
            method="POST",
            path="/api/cron/auto-initiate/run-single",
            body=body,
        )

    async def _fetch_bot_crons(
        self,
        bot: dict,
        user_id: str,
    ) -> dict:
        """获取单个 bot 的 cron 列表"""
        bot_id = bot.get("bot_id")
        binding_id = bot.get("binding_id")

        if not binding_id:
            logger.warning(f"[_fetch_bot_crons] Bot {bot_id} has no binding_id")
            return {"success": True, "data": []}

        # 检查设备状态是否为 ACTIVE
        try:
            device = self._device_provider.get_device(binding_id=binding_id)
            device_status = device.status if hasattr(device, 'status') else device.get("status")
            if device_status != DeviceBindingStatus.ACTIVE:
                logger.warning(f"[_fetch_bot_crons] Bot {bot_id} device not ACTIVE (status={device_status}), skipping")
                return {"success": True, "data": []}
        except Exception as e:
            logger.warning(f"[_fetch_bot_crons] Failed to get device status for bot {bot_id}: {e}")
            return {"success": False, "error": f"Device not available: {e}"}

        # 通过 DeviceContextResolver 拿 DeviceContext(全仓唯一 provider 解析点),
        # 替代旧的 ``device_service.get_device_connection_v2(binding_id, user_id, nick_name)``。
        try:
            ctx = self._resolver.resolve_for_bot(bot_id, user_id)
        except Exception as e:
            logger.error(f"[_fetch_bot_crons] Failed to resolve device context for bot {bot_id}: {e}")
            return {"success": False, "error": str(e)}

        # 通过中继 transport 请求 Adapter — conn_info 仍是 dict,字段语义不变
        try:
            return await self._transport.invoke(ctx.conn_info, "GET", "/api/cron")
        except Exception as e:
            logger.error(f"[_fetch_bot_crons] Adapter request failed for bot {bot_id}: {e}")
            return {"success": False, "error": str(e)}

    def _build_runtime_targets(
        self,
        bot: dict,
        user_id: str,
    ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
        bot_id = bot.get("bot_id")
        bot_name = bot.get("bot_name", "")
        owner_id = bot.get("owner_id") or user_id
        bot_type = bot.get("bot_type") or "personal"
        targets: list[CronRuntimeTarget] = []
        failed_targets: list[dict[str, Any]] = []

        binding_id = bot.get("binding_id")
        if binding_id:
            targets.append(
                CronRuntimeTarget(
                    bot_id=bot_id,
                    bot_name=bot_name,
                    owner_id=owner_id,
                    bot_type=bot_type,
                    runtime_stage=RUNTIME_STAGE_DRAFT,
                    binding_id=binding_id,
                )
            )
        else:
            failed_targets.append(
                self._failed_target_from_values(
                    bot_id=bot_id,
                    bot_name=bot_name,
                    owner_id=owner_id,
                    runtime_stage=RUNTIME_STAGE_DRAFT,
                    publish_id=None,
                    reason="binding_missing",
                    message=f"Bot {bot_id} has no draft binding_id",
                )
            )

        if bot_type != "service":
            return targets, failed_targets

        for runtime_stage, publish_status, binding_key in (
            (RUNTIME_STAGE_VERIFY, PublishStatus.VALIDATING.value, "verify"),
            (RUNTIME_STAGE_ONLINE, PublishStatus.SUCCESS.value, "online"),
        ):
            try:
                publish_record = (
                    self._publish_repo.get_latest_by_source_bot_id_and_owner_and_status(
                        source_bot_id=bot_id,
                        owner_id=owner_id,
                        status=publish_status,
                        env=get_current_env(),
                    )
                )
            except Exception as e:
                failed_targets.append(
                    self._failed_target_from_values(
                        bot_id=bot_id,
                        bot_name=bot_name,
                        owner_id=owner_id,
                        runtime_stage=runtime_stage,
                        publish_id=None,
                        reason="publish_query_failed",
                        message=str(e),
                    )
                )
                continue

            if publish_record is None:
                continue

            binding_info = (publish_record.ext or {}).get("binding") or {}
            stage_binding_id = binding_info.get(binding_key)
            publish_id = getattr(publish_record, "id", None)
            if not stage_binding_id:
                failed_targets.append(
                    self._failed_target_from_values(
                        bot_id=bot_id,
                        bot_name=bot_name,
                        owner_id=owner_id,
                        runtime_stage=runtime_stage,
                        publish_id=publish_id,
                        reason="binding_missing",
                        message=f"publish record has no ext.binding.{binding_key}",
                    )
                )
                continue

            targets.append(
                CronRuntimeTarget(
                    bot_id=bot_id,
                    bot_name=bot_name,
                    owner_id=owner_id,
                    bot_type=bot_type,
                    runtime_stage=runtime_stage,
                    binding_id=stage_binding_id,
                    publish_id=publish_id,
                    publish_status=getattr(publish_record, "status", publish_status),
                )
            )

        return targets, failed_targets

    def _resolve_published_runtime_target(
        self,
        bot: dict,
        user_id: str,
        runtime_stage: str,
    ) -> CronRuntimeTarget:
        bot_id = bot.get("bot_id")
        owner_id = bot.get("owner_id") or user_id
        bot_type = bot.get("bot_type") or "personal"
        if bot_type != "service":
            raise CronRelayError(
                f"runtime_stage={runtime_stage} only supports service bot",
                error_code=400,
            )

        publish_status, binding_key = {
            RUNTIME_STAGE_VERIFY: (PublishStatus.VALIDATING.value, "verify"),
            RUNTIME_STAGE_ONLINE: (PublishStatus.SUCCESS.value, "online"),
        }[runtime_stage]
        publish_record = self._publish_repo.get_latest_by_source_bot_id_and_owner_and_status(
            source_bot_id=bot_id,
            owner_id=owner_id,
            status=publish_status,
            env=get_current_env(),
        )
        if publish_record is None:
            raise CronRelayError(
                f"runtime_stage={runtime_stage} publish record not found",
                error_code=404,
            )

        binding_info = (publish_record.ext or {}).get("binding") or {}
        binding_id = binding_info.get(binding_key)
        if not binding_id:
            raise CronRelayError(
                f"runtime_stage={runtime_stage} binding not found",
                error_code=404,
            )

        return CronRuntimeTarget(
            bot_id=bot_id,
            bot_name=bot.get("bot_name", ""),
            owner_id=owner_id,
            bot_type=bot_type,
            runtime_stage=runtime_stage,
            binding_id=binding_id,
            publish_id=getattr(publish_record, "id", None),
            publish_status=getattr(publish_record, "status", publish_status),
        )

    def _validate_runtime_operation(
        self,
        *,
        runtime_stage: str,
        method: str,
        body: Optional[dict],
    ) -> None:
        if runtime_stage not in VALID_RUNTIME_STAGES:
            raise CronRelayError(f"Invalid runtime_stage: {runtime_stage}", error_code=400)

        if runtime_stage == RUNTIME_STAGE_DRAFT:
            return

        method_upper = method.upper()
        if method_upper == "DELETE":
            raise CronRelayError("发布态定时任务不支持删除", error_code=403)

        if method_upper == "PUT":
            body = body or {}
            if set(body.keys()) != {"enabled"}:
                raise CronRelayError(
                    "发布态定时任务不支持编辑，仅允许启停",
                    error_code=403,
                )
            if not isinstance(body.get("enabled"), bool):
                raise CronRelayError("enabled must be bool", error_code=400)

    async def forward_request(
        self,
        bot_id: str,
        user_id: str,
        nick_name: str,
        method: str,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        runtime_stage: str = RUNTIME_STAGE_DRAFT,
    ) -> dict:
        """转发请求到指定 Bot 的 Adapter

        Args:
            bot_id: Bot ID
            user_id: 用户ID
            nick_name: 用户花名
            method: HTTP 方法 (GET/POST/PUT/DELETE)
            path: 路径 (如 "/api/cron/{task_id}")
            body: 请求体
            params: 查询参数

        Returns:
            Adapter 的响应
        """
        self._validate_runtime_operation(
            runtime_stage=runtime_stage,
            method=method,
            body=body,
        )

        # 1. 获取 bot 信息（隐式权限检查）
        bot = self._bot_provider.get_bot(bot_id, user_id)
        if runtime_stage == RUNTIME_STAGE_DRAFT:
            binding_id = bot.get("binding_id")

            if not binding_id:
                raise ValueError(f"Bot {bot_id} has no device binding")
            target = CronRuntimeTarget(
                bot_id=bot_id,
                bot_name=bot.get("bot_name", ""),
                owner_id=bot.get("owner_id") or user_id,
                bot_type=bot.get("bot_type") or "personal",
                runtime_stage=RUNTIME_STAGE_DRAFT,
                binding_id=binding_id,
            )
        else:
            target = self._resolve_published_runtime_target(bot, user_id, runtime_stage)

        # 检查设备状态是否为 ACTIVE
        try:
            device = self._device_provider.get_device(binding_id=target.binding_id)
            device_status = self._read_field(device, "status")
            if device_status != DeviceBindingStatus.ACTIVE:
                raise CronRelayError(
                    f"Bot {bot_id} runtime_stage={runtime_stage} "
                    f"device not ACTIVE (status={device_status})",
                    error_code=409,
                )
        except Exception as e:
            logger.error(f"[forward_request] Device status check failed for bot {bot_id}: {e}")
            if isinstance(e, CronRelayError):
                raise
            raise ValueError(f"Device not available: {e}")

        if self._should_fan_out_runtime_operation(
            target,
            method=method,
            path=path,
        ):
            expanded_targets, failed_targets = self._expand_runtime_target(
                target,
                device=device,
            )
            if any(expanded.device_uuid for expanded in expanded_targets):
                result = await self._forward_multi_instance_request(
                    expanded_targets,
                    method=method,
                    path=path,
                    body=body,
                    params=params,
                    failed_targets=failed_targets,
                )
                logger.info(
                    "[forward_request] Fan-out for bot %s stage=%s: %s %s "
                    "succeeded=%s failed=%s",
                    bot_id,
                    runtime_stage,
                    method,
                    path,
                    result["data"]["succeeded"],
                    result["data"]["failed"],
                )
                return result
            if failed_targets and not expanded_targets:
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

        # 2. 通过 DeviceContextResolver 拿 DeviceContext(全仓唯一 provider 解析点),
        # 替代旧的 ``get_device_connection_v2`` — 后者对 baas service bot 落 direct
        # 分支丢 binding_id,transport fallback 裸 httpx 直发 ARCA-SANDBOX 内网 → 500。
        # 走 resolver → 永填 binding_id → transport 走 baas proxypass。
        ctx = self._resolve_runtime_context(target)

        # 3. 通过中继 transport 转发请求
        result = await self._transport.invoke(ctx.conn_info, method, path, body, params)

        # 在返回数据中添加 bot_id、bot_name 和 owner_id
        if result.get("success") and result.get("data") and isinstance(result["data"], dict):
            result["data"]["bot_id"] = bot_id
            result["data"]["bot_name"] = bot.get("bot_name", "")
            result["data"]["owner_id"] = bot.get("owner_id") or user_id
            if runtime_stage != RUNTIME_STAGE_DRAFT:
                result["data"]["runtime_stage"] = runtime_stage
                if target.publish_id is not None:
                    result["data"]["publish_id"] = target.publish_id
                if target.publish_status is not None:
                    result["data"]["publish_status"] = target.publish_status

        logger.info(
            "[forward_request] Success for bot %s stage=%s: %s %s",
            bot_id,
            runtime_stage,
            method,
            path,
        )
        return result
