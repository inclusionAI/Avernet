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
from agentclaw.community.core.repository.protocols.bot import TemplateRepository
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
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
    CronRuntimeTargetMixin,
    RUNTIME_STAGE_DRAFT,
    RUNTIME_STAGE_ONLINE,
    RUNTIME_STAGE_VERIFY,
    RUNTIME_QUERY_PREPARE_CONCURRENCY,
    VALID_RUNTIME_STAGES,
)
from agentclaw.community.core.cron.services.cron_runtime_operations import (
    CronRuntimeOperationsMixin,
)
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

logger = get_logger()

_CRON_UNSUPPORTED_ENGINES = frozenset({"hermes"})


class CronRelayService(CronRuntimeOperationsMixin, CronRuntimeTargetMixin):
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
        # Cron 批量查询共用限流器，控制同步设备连接准备占用的线程数。
        self._runtime_query_prepare_semaphore = asyncio.Semaphore(
            RUNTIME_QUERY_PREPARE_CONCURRENCY
        )

    async def list_all_crons(
        self,
        user_id: str,
        nick_name: str,
        bot_id: Optional[str] = None,
        runtime_stage: Optional[str] = None,
    ) -> dict:
        """获取用户所有 Bots 的定时任务（平铺展示）

        Args:
            user_id: 用户ID
            nick_name: 用户花名
            bot_id: 如果为 "all" 或 None，返回所有 bots 的任务；否则返回指定 bot 的任务
            runtime_stage: 仅返回该运行态的任务；None 表示全部运行态。openapi_v1
                传 draft——公开面只操作草稿态；内部控制台不传，保持全运行态聚合。

        Returns:
            {"success": True, "data": [...], "total": N}
        """
        if runtime_stage is not None and runtime_stage not in VALID_RUNTIME_STAGES:
            raise CronRelayError(
                f"Invalid runtime_stage: {runtime_stage}", error_code=400
            )
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

        # 2. 获取每个 bot 的 runtime target，再并发获取 cron 配置列表。
        # Cron 配置在同一运行态的多实例间保持一致，列表只展示到 stage 维度；
        # running/runs/启停/触发才需要实例维度。
        targets: list[CronRuntimeTarget] = []
        failed_targets: list[dict[str, Any]] = []
        for bot in bots:
            bot_targets, bot_failed_targets = self._build_runtime_targets(bot, user_id)
            targets.extend(bot_targets)
            failed_targets.extend(bot_failed_targets)

        if runtime_stage is not None:
            # 过滤在取数之前：范围之外的运行态既不查询也不产生失败项。
            targets = [t for t in targets if t.runtime_stage == runtime_stage]
            failed_targets = [
                t for t in failed_targets if t.get("runtime_stage") == runtime_stage
            ]

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
        bot_id: str,
        runtime_stage: str | None = None,
        device_uuid: str | None = None,
    ) -> dict:
        """获取指定 Bot 的运行中任务。

        服务 Bot 必须指定 runtime_stage，并按运行实例聚合；个人 Bot 不接收
        runtime_stage 或 device_uuid。
        """
        # running 只查询一个 Bot，不接受聚合全部 Bot 的 all 参数。
        if not bot_id or bot_id == "all":
            raise CronRelayError("a specific bot_id is required", error_code=400)
        if runtime_stage is not None and runtime_stage not in VALID_RUNTIME_STAGES:
            raise CronRelayError(f"Invalid runtime_stage: {runtime_stage}", error_code=400)
        if device_uuid and not runtime_stage:
            raise CronRelayError("device_uuid requires runtime_stage", error_code=400)

        # 根据 Bot 类型校验 stage，并构造唯一的运行态范围。
        bot = self._bot_provider.get_bot(bot_id, user_id)
        bot_type = bot.get("bot_type") or "personal"
        if bot_type == "service":
            if runtime_stage is None:
                raise CronRelayError(
                    "runtime_stage is required for service bot",
                    error_code=400,
                )
        else:
            if runtime_stage is not None:
                raise CronRelayError(
                    "runtime_stage only supports service bot",
                    error_code=400,
                )
            if device_uuid:
                raise CronRelayError(
                    "device_uuid only supports service bot",
                    error_code=400,
                )

        targets, failed_targets = self._build_runtime_targets(bot, user_id)
        if runtime_stage is not None:
            targets = [
                target
                for target in targets
                if target.runtime_stage == runtime_stage
            ]
            failed_targets = [
                target
                for target in failed_targets
                if target.get("runtime_stage") == runtime_stage
            ]

        # 服务 Bot 的多实例 stage 展开为带 device_uuid 的实际查询目标。
        targets, instance_failed_targets = await self._expand_runtime_targets(targets)
        failed_targets.extend(instance_failed_targets)
        if device_uuid:
            expanded_targets = targets
            targets = [
                target
                for target in expanded_targets
                if target.device_uuid == device_uuid
            ]
            if expanded_targets and not targets:
                raise CronRelayError(
                    (
                        f"device_uuid={device_uuid} not found for "
                        f"runtime_stage={runtime_stage}"
                    ),
                    error_code=404,
                )

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
            if result.get("success") is False:
                failed_targets.append(
                    self._failed_target(
                        target,
                        result.get("reason") or "cron_api_failed",
                        result.get("error") or result.get("message") or "cron api failed",
                    )
                )
                continue
            data = result.get("data", {})
            if isinstance(data, dict):
                running_list = data.get("running", [])
                for item in running_list:
                    self._decorate_runtime_item(item, target)
                all_running.extend(running_list)

        return {"success": True, "data": all_running, "failed_targets": failed_targets}

    async def find_auto_initiate_and_run(
        self,
        bot_id: str,
        user_id: str,
        nick_name: str,
        force: bool = True,
    ) -> dict:
        """查找 Bot 的 autoInitiate 类型定时任务并触发执行。

        Args:
            bot_id: Bot ID
            user_id: 用户ID
            nick_name: 用户花名（死参，缺省用 user_id）
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
        """为单个需求直接发起会话。

        workflow 从 bot 的 template_config.ext.devflow_workflow 中读取，
        与 cron_auto_setup 保持一致，调用方无需传入。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID
            nick_name: 用户花名
            dima_url: 需求 URL
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

        # 构造该 bot 当前 binding 的 adapter 连接上下文。
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

    def _get_retained_verify_publish_record(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> Any | None:
        """返回生产发布单中仍可使用的 verify 运行态。

        当 Vn+1 处于 VALIDATING 时，调用方优先使用 Vn+1 的 verify binding。
        只有不存在 VALIDATING 发布单时，才检查 Vn 的 SUCCESS 发布单；其中的
        verify binding 仍为 ACTIVE，表示该预发运行态在生产发布后仍被保留。
        """
        publish_record = (
            self._publish_repo.get_latest_by_source_bot_id_and_owner_and_status(
                source_bot_id=bot_id,
                owner_id=owner_id,
                status=PublishStatus.SUCCESS.value,
                env=get_current_env(),
            )
        )
        if publish_record is None:
            return None

        binding_info = (publish_record.ext or {}).get("binding") or {}
        verify_binding_id = binding_info.get("verify")
        if not verify_binding_id:
            return None

        try:
            device = self._device_provider.get_device(binding_id=verify_binding_id)
        except Exception as e:
            logger.warning(
                "Failed to read retained verify binding for bot %s: %s",
                bot_id,
                e,
            )
            return None

        if self._read_field(device, "status") != DeviceBindingStatus.ACTIVE:
            return None
        return publish_record

    def _build_runtime_targets(
        self,
        bot: dict,
        user_id: str,
    ) -> tuple[list[CronRuntimeTarget], list[dict[str, Any]]]:
        """构造 Bot 可用的 draft、verify 和 online 运行态目标。

        个人 Bot 只生成当前 binding 目标；服务 Bot 还会读取验证中和已发布记录。
        不支持 cron 的引擎不生成目标；缺失 binding 或发布记录查询失败时返回失败项。
        """
        active_engine = str(bot.get("active_engine") or "").strip().lower()
        if active_engine in _CRON_UNSUPPORTED_ENGINES:
            return [], []

        bot_id = bot.get("bot_id")
        bot_name = bot.get("bot_name", "")
        owner_id = bot.get("owner_id") or user_id
        bot_type = bot.get("bot_type") or "personal"
        targets: list[CronRuntimeTarget] = []
        failed_targets: list[dict[str, Any]] = []

        # 当前 binding 对应 draft；个人 Bot 构造完该目标后即可返回。
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
                    runtime_stage=(
                        RUNTIME_STAGE_DRAFT if bot_type == "service" else None
                    ),
                    publish_id=None,
                    reason="binding_missing",
                    message=f"Bot {bot_id} has no draft binding_id",
                )
            )

        if bot_type != "service":
            return targets, failed_targets

        # verify 优先使用验证中版本；online 使用发布成功版本。
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
                # 没有验证中版本时，生产发布单里仍为 ACTIVE 的 verify binding
                # 继续代表可用的预发运行态。
                if publish_record is None and runtime_stage == RUNTIME_STAGE_VERIFY:
                    publish_record = self._get_retained_verify_publish_record(
                        bot_id=bot_id,
                        owner_id=owner_id,
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
        """解析服务 Bot 指定发布态的 binding 和发布元数据。"""
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
        # 指定 verify 时遵循与列表相同的版本优先级，避免列表可见但操作无法路由。
        if publish_record is None and runtime_stage == RUNTIME_STAGE_VERIFY:
            publish_record = self._get_retained_verify_publish_record(
                bot_id=bot_id,
                owner_id=owner_id,
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
        """校验发布态允许执行的定时任务操作。"""
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
            device_status = device.status if hasattr(device, 'status') else device.get("status")
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

        # 2. 构造目标运行态的 adapter 连接上下文。
        if runtime_stage == RUNTIME_STAGE_DRAFT:
            ctx = self._resolver.resolve_for_bot(bot_id, user_id)
        else:
            ctx = self._resolver.resolve_for_binding(
                target.binding_id,
                user_id,
                bot_id=bot_id,
            )

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
