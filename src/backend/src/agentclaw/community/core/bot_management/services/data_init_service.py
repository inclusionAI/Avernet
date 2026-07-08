"""
DataInitService -- Bot 冷启动数据初始化编排服务。

混合编排流程（LLM + 确定性代码）：
1. 白名单检查（灰度控制）
2. 幂等检查（Bot.ext.data_init_status）
3. 读取 Bot 完整信息 + LINK 资源 + mcp_codes
4. 通过 Engine WebSocket 发消息，让 LLM 读取设备上的 SKILL.md 并执行：
   - LLM 通过 mcporter 调用 MCP 工具拉取外部数据
   - LLM 基于拉取的数据生成 summary（capability + role）
   - LLM 返回结构化 JSON（summary + raw_data）
5. Backend 解析 LLM 返回的 JSON
6. Backend 调用 sync_to_downstream 同步到 ECB + BCS Fuse（fire-and-forget）
7. 更新 data_init_status 为 completed（下游失败不影响）
8. 重试机制（指数退避，最多 3 次）

触发时机：
- 设备变为 ACTIVE 时（唯一触发点）
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.devices.services.device_service import DeviceService
    from agentclaw.community.core.resources.repository.protocol import (
        ResourceRepositoryProtocol,
    )
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetActivatorFactory,
    )
    from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor

logger = get_logger()

# 设备上的 data-init SKILL.md 路径由 ``SkillRepoSyncPlugin.get_data_init_skill_md_path``
# 提供 —— local/prod 各自的实现知道自己布局。

# LLM 结果标记
_RESULT_BEGIN = "DATA_INIT_RESULT_BEGIN"
_RESULT_END = "DATA_INIT_RESULT_END"

# LLM 超时（秒）
_LLM_TIMEOUT = int(os.getenv("DATA_INIT_LLM_TIMEOUT", "300"))

# WS 事件等待超时（秒）— 等待 Engine 推送 LLM 完成事件
_WS_EVENT_TIMEOUT = int(os.getenv("DATA_INIT_WS_EVENT_TIMEOUT", "300"))

# WS 无内容超时（秒）— 连续只收到 tick 事件、没有 content/delta 时提前退出
_WS_NO_CONTENT_TIMEOUT = int(
    os.getenv("DATA_INIT_WS_NO_CONTENT_TIMEOUT", "60")
)

# 临时方案：Gateway 重启注入的错误消息特征文本
# 当 report_device_status(SUCCEEDED) 时 Gateway 可能还未完全启动，
# 此时创建的 session 会被 Engine 注入该错误消息，需要检测并删除 session
_GATEWAY_RESTART_MARKER = "Gateway is restarting"


class DataInitService:
    """Bot 冷启动数据初始化编排服务。

    状态机：null -> pending_init -> in_progress -> completed / failed
    """

    def __init__(
        self,
        resource_repo: "ResourceRepositoryProtocol",
        device_service: "DeviceService",
        skill_set_factory: "SkillSetServiceFactory",
        skill_set_activator_factory: "SkillSetActivatorFactory",
        device_plugin: "DeviceAccessor",
        bot_service_provider: Callable[[], "BotService"],
        skill_md_path: str,
        resolver: "DeviceContextResolver",
        bcsfuse_base_url: str = "",
        ecb_base_url: str = "",
    ) -> None:
        # ``bot_service_provider`` is lazy — BotService depends on
        # DataInitService transitively via DeviceService's
        # data_init_service_provider, so eager injection would close the
        # cycle.
        #
        # ``resolver`` 是全仓唯一 provider 解析点 — ``_get_engine_connection``
        # 通过 (bot_id, owner_id) 拿 typed ``DeviceContext`` 替代旧的
        # ``device_service.get_device_connection_v2(binding_id, ...)``。
        #
        # ``skill_set_activator_factory`` — Task 6 收口后,_activate_and_sync_skill_sets
        # 走 factory.create(...) 拿 activator,避免在业务层手拼 ctor 参数。
        self._resource_repo = resource_repo
        self._device_service = device_service
        self._skill_set_factory = skill_set_factory
        self._skill_set_activator_factory = skill_set_activator_factory
        self._device_plugin = device_plugin
        self._bot_service_provider = bot_service_provider
        self._skill_md_path = skill_md_path
        self._resolver = resolver
        self._bcsfuse_base_url = bcsfuse_base_url
        self._ecb_base_url = ecb_base_url

    MAX_RETRIES = 3
    # 首次重试等 10s，后续翻倍：10 → 20 → 40
    # 覆盖 Agent runtime 初始化场景（MCP 连接、LLM 客户端加载等）
    RETRY_BASE_DELAY_SECONDS = 10

    # in_progress 超时阈值（秒）：超过此时间视为卡死，允许重新执行
    _IN_PROGRESS_TIMEOUT_SECONDS = int(os.getenv("DATA_INIT_IN_PROGRESS_TIMEOUT", "600"))

    async def trigger_init(
        self,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        force: bool = False,
    ) -> dict:
        """触发 Bot 数据初始化流程。

        根据 Bot 当前状态决定立即执行还是标记 pending_init：
        - Bot ACTIVE -> 直接执行 data-init
        - Bot 非 ACTIVE -> 标记 data_init_status="pending_init"，等设备心跳回调触发

        状态机：null -> pending_init -> in_progress -> completed / failed

        Args:
            bot_id: Bot ID
            owner_id: Bot owner 的 user ID
            entity_id: 关联实体 ID
            entity_type: 关联实体类型（staff/proj/team）
            force: 是否强制重新初始化（忽略 completed 状态）

        Returns:
            dict with keys: status, message
        """
        logger.info(
            f"bot_id={bot_id} trigger_init entry "
            f"owner_id={owner_id} entity_id={entity_id} entity_type={entity_type} force={force}"
        )

        bot_service = self._bot_service_provider()

        # 1. 幂等检查（force=True 时跳过）
        if not self._should_run_init(bot_id, owner_id, bot_service, force=force):
            logger.info(f"bot_id={bot_id} trigger_init skipped idempotency_check")
            return {"status": "skipped", "message": "初始化已完成或正在进行中"}

        # 2. 获取 Bot 信息，判断 Bot 状态
        try:
            bot = bot_service.get_bot(bot_id, owner_id)
        except Exception as exc:
            logger.error(f"bot_id={bot_id} trigger_init get_bot_failed exc={exc}")
            return {"status": "error", "message": f"获取 Bot 信息失败: {exc}"}

        bot_status = bot.get("status", "UNKNOWN")
        ext_for_log = bot.get("ext") or {}
        if isinstance(ext_for_log, str):
            try:
                ext_for_log = json.loads(ext_for_log)
            except json.JSONDecodeError:
                ext_for_log = {}
        logger.info(
            f"bot_id={bot_id} trigger_init bot_snapshot "
            f"bot_status={bot_status} data_init_status={ext_for_log.get('data_init_status')} "
            f"binding_id={bot.get('binding_id')} device_id={bot.get('device_id')}"
        )

        if bot_status == "ACTIVE":
            # 再次检查 data_init_status，防止并发重复触发
            ext = bot.get("ext") or {}
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except json.JSONDecodeError:
                    ext = {}
            current_init_status = ext.get("data_init_status")
            if current_init_status == "in_progress":
                logger.info(
                    f"bot_id={bot_id} trigger_init skipped already_in_progress"
                )
                return {"status": "skipped", "message": "初始化正在进行中"}

            # Bot 已就绪，设为 in_progress 并记录开始时间，直接 await 执行
            bot_service.update_bot_ext(bot_id, owner_id, {
                "data_init_status": "in_progress",
                "data_init_started_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.info(
                f"bot_id={bot_id} trigger_init branch_active awaiting_execution"
            )

            # 由调用方决定是否即发即弃（asyncio.create_task / ensure_future）或 await
            success = await self._async_execute_with_retry(
                bot_id, owner_id, entity_id, entity_type, bot_service
            )
            if success:
                return {"status": "completed", "message": "初始化已完成"}
            else:
                return {"status": "failed", "message": "初始化失败，所有重试已耗尽"}
        else:
            # Bot 还没就绪，标记 pending_init，等设备心跳回调触发
            bot_service.update_bot_ext(bot_id, owner_id, {"data_init_status": "pending_init"})
            logger.info(
                f"bot_id={bot_id} trigger_init branch_pending "
                f"bot_status={bot_status} marked=pending_init waiting=device_callback"
            )
            return {"status": "pending_init", "message": "等待 Bot 就绪后自动执行"}

    async def _async_execute_with_retry(
        self,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        bot_service: Any,
    ) -> bool:
        """异步执行 data-init（带重试）。

        Returns:
            True 表示成功，False 表示所有重试失败。
        """
        logger.info(f"bot_id={bot_id} execute_with_retry start")
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.info(f"bot_id={bot_id} execute_with_retry attempt={attempt}/{self.MAX_RETRIES}")
                await self._execute_init(bot_id, owner_id, entity_id, entity_type, bot_service)
                logger.info(f"bot_id={bot_id} execute_with_retry success")
                return True
            except Exception as exc:
                logger.warning(
                    f"bot_id={bot_id} execute_with_retry attempt={attempt}/{self.MAX_RETRIES} failed exc={exc}",
                    exc_info=True,
                )
                if attempt < self.MAX_RETRIES:
                    delay = self.RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    logger.info(f"bot_id={bot_id} execute_with_retry retry_in={delay}s")
                    await asyncio.sleep(delay)

        # 所有重试失败
        logger.error(f"bot_id={bot_id} execute_with_retry all_attempts_failed total={self.MAX_RETRIES}")
        bot_service.update_bot_ext(bot_id, owner_id, {
            "data_init_status": "failed",
            "data_init_started_at": None,
        })
        return False

    def _should_run_init(
        self, bot_id: str, owner_id: str, bot_service: Any, force: bool = False
    ) -> bool:
        """幂等检查：判断是否应该执行初始化。

        Args:
            force: True 时跳过 completed 状态检查（允许重新初始化）

        Returns:
            True 表示应该执行，False 表示跳过。
        """
        try:
            bot = bot_service.get_bot(bot_id, owner_id)
        except Exception as exc:
            logger.error(f"bot_id={bot_id} should_run_init get_bot_failed exc={exc}")
            return False

        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                ext = {}

        status = ext.get("data_init_status")

        # in_progress 时检查是否超时卡死
        if status == "in_progress":
            started_at_str = ext.get("data_init_started_at")
            if started_at_str:
                try:
                    started_at = datetime.fromisoformat(started_at_str)
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
                    if elapsed_seconds > self._IN_PROGRESS_TIMEOUT_SECONDS:
                        logger.warning(
                            f"bot_id={bot_id} should_run_init in_progress_timeout "
                            f"elapsed={elapsed_seconds:.0f}s limit={self._IN_PROGRESS_TIMEOUT_SECONDS}s "
                            f"re_executing=true"
                        )
                        return True
                except (ValueError, TypeError) as parse_exc:
                    logger.warning(
                        f"bot_id={bot_id} should_run_init parse_started_at_failed "
                        f"started_at={started_at_str} exc={parse_exc} re_executing=true"
                    )
                    return True

            logger.info(
                f"bot_id={bot_id} should_run_init skipped data_init_status=in_progress"
            )
            return False

        # completed 时，force=True 才允许重新执行
        if status == "completed" and not force:
            logger.info(
                f"bot_id={bot_id} should_run_init skipped data_init_status=completed force=False"
            )
            return False

        return True

    async def _execute_init(
        self,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        bot_service: Any,
    ) -> None:
        """执行一次完整的初始化流程（不含重试）。

        混合编排：
        1. 收集 Bot 的 LINK 资源
        2. 通过 Engine WebSocket 发消息，让 LLM 读取设备上的 SKILL.md 并执行数据初始化
        3. 解析 LLM 返回的 JSON（summary + raw_data）
        4. 调用 sync_to_downstream 同步到 ECB + BCS Fuse（fire-and-forget）
        5. 更新 data_init_status
        """

        import time as _time
        logger.info(f"bot_id={bot_id} execute_init start")

        # 1. 收集 Bot 的 LINK 资源
        _t_collect = _time.time()
        bot_info = self._collect_bot_info(bot_id, owner_id, bot_service)
        logger.info(
            f"bot_id={bot_id} execute_init bot_info_collected "
            f"binding_id={bot_info.get('binding_id')} "
            f"link_count={len(bot_info.get('link_resources', []))} "
            f"bot_name={bot_info.get('bot_name', '')[:50]} "
            f"cost_ms={(_time.time() - _t_collect) * 1000:.0f}"
        )

        # 1.5 激活所有 skillset 并同步软链到设备（失败不阻塞）
        _t_skill = _time.time()
        try:
            await self._activate_and_sync_skill_sets(bot_id, owner_id, entity_id)
            logger.info(
                f"bot_id={bot_id} execute_init skillset_sync_done "
                f"cost_ms={(_time.time() - _t_skill) * 1000:.0f}"
            )
        except Exception as e:
            logger.warning(
                f"bot_id={bot_id} execute_init skillset_sync_failed exc={e} "
                f"cost_ms={(_time.time() - _t_skill) * 1000:.0f} non_blocking=true",
                exc_info=True,
            )

        # 2. 通过 Engine 让 LLM 执行 data-init Skill
        # _send_to_engine 内部使用同步 I/O（requests + websocket-client），
        # 通过 asyncio.to_thread() 在线程池中执行，避免阻塞 asyncio 事件循环
        _t_llm = _time.time()
        logger.info(f"bot_id={bot_id} execute_init send_to_engine_start")
        llm_result = await asyncio.to_thread(
            self._send_to_engine,
            bot_id=bot_id,
            owner_id=owner_id,
            binding_id=bot_info.get("binding_id"),
            link_resources=bot_info.get("link_resources", []),
            bot_name=bot_info.get("bot_name", ""),
            bot_description=bot_info.get("bot_description", ""),
            iam_token=bot_info.get("iam_token", ""),
        )
        logger.info(
            f"bot_id={bot_id} execute_init send_to_engine_done "
            f"cost_ms={(_time.time() - _t_llm) * 1000:.0f} "
            f"summary_keys={list(llm_result.get('summary', {}).keys())} "
            f"raw_data_types={list(llm_result.get('raw_data', {}).keys())} "
            f"fetch_errors={llm_result.get('fetch_errors', [])}"
        )

        summary = llm_result.get("summary", {})

        # 3. 同步到 ECB + BCS Fuse（fire-and-forget）
        # 下游同步失败不影响初始化状态，只打日志 + 写入 ext 供查询
        downstream_sync = {
            "ecb": {"success": False},
            "bcsfuse": {"success": False},
        }
        try:
            from agentclaw.community.core.bot_management.services.sync_to_downstream import sync_all
            sync_result = sync_all(
                bot_id,
                owner_id,
                summary,
                self._resource_repo,
                self._bot_service_provider(),
                bcsfuse_base_url=self._bcsfuse_base_url,
                ecb_base_url=self._ecb_base_url,
            )
            downstream_sync = {
                "ecb": {"success": sync_result.get("ecb", {}).get("success", False)},
                "bcsfuse": {"success": sync_result.get("bcsfuse", {}).get("success", False)},
            }
            # 记录错误信息（如果有）
            ecb_error = sync_result.get("ecb", {}).get("error")
            if ecb_error:
                downstream_sync["ecb"]["error"] = ecb_error
            bcsfuse_error = sync_result.get("bcsfuse", {}).get("error")
            if bcsfuse_error:
                downstream_sync["bcsfuse"]["error"] = bcsfuse_error

            logger.info(
                f"bot_id={bot_id} execute_init downstream_sync_done "
                f"ecb={downstream_sync['ecb']['success']} bcsfuse={downstream_sync['bcsfuse']['success']}"
            )
        except Exception as sync_exc:
            downstream_sync["error"] = str(sync_exc)
            logger.warning(
                f"bot_id={bot_id} execute_init downstream_sync_failed exc={sync_exc} non_blocking=true",
                exc_info=True,
            )

        # 4. 只要 LLM 返回了有效结果，就标记 completed（下游同步失败不影响）
        bot_service.update_bot_ext(bot_id, owner_id, {
            "data_init_status": "completed",
            "data_init_started_at": None,
            "downstream_sync": downstream_sync,
        })
        logger.info(f"bot_id={bot_id} execute_init completed")

    async def _activate_and_sync_skill_sets(
        self,
        bot_id: str,
        owner_id: str,
        entity_id: str,
    ) -> None:
        """激活所有 skillset 并同步软链到设备。

        1. 对每个 skillset 调用 activate（未激活的走完整流程，已激活的跳过）
        2. 无条件补一次 device sync（处理已激活但缺软链的情况）

        engine_type 必须按 bot 真实 active_engine 透传，否则
        SkillSetService.__init__ 会把 engine_type=None 兜底成 openclaw,
        导致 claude_code 等 engine 的 bot 在 data_init 全量同步时被
        错按 openclaw 捞技能集 / 算软链路径（曾把 claude_code 已下的
        10 个软链覆盖成 openclaw 默认集的 4 个）。
        """
        # 取 bot 真实 active_engine；取不到时降级 DEFAULT_ENGINE_TYPE 继续走完流程，
        # 不让本步骤的失败整段跳过 skillset 激活（保持修复前的容错性）。
        engine_type = DEFAULT_ENGINE_TYPE
        try:
            bot = self._bot_service_provider().get_bot(bot_id, owner_id)
            engine_type = bot.get("active_engine") or DEFAULT_ENGINE_TYPE
        except Exception as exc:
            logger.warning(
                f"bot_id={bot_id} activate_and_sync_skill_sets get_bot failed, "
                f"fallback engine_type={engine_type} exc={exc}",
                exc_info=True,
            )

        activator = self._skill_set_activator_factory.create(
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
        )
        skill_sets = activator.skill_set_service.skill_set_repo.list_all(
            user_id=entity_id, bolt_id=bot_id
        )
        logger.info(
            f"bot_id={bot_id} activate_and_sync_skill_sets found skill_sets={len(skill_sets)} entity_id={entity_id}"
        )

        for ss in skill_sets:
            ss_id = str(ss.get('id', ''))
            result = await activator.activate_skill_set(ss_id, user_id=owner_id)
            logger.info(
                f"bot_id={bot_id} activate_and_sync_skill_sets activate skill_set={ss_id} "
                f"success={result.success} message={result.message}"
            )

        # 无条件同步软链到设备（全量覆盖）
        sync_result = activator._do_device_sync(
            user_id=owner_id, caller="DataInitService"
        )
        logger.info(f"bot_id={bot_id} activate_and_sync_skill_sets device_sync result={sync_result}")

    def _collect_bot_info(
        self,
        bot_id: str,
        owner_id: str,
        bot_service: Any,
    ) -> dict:
        """收集 Bot 信息。

        返回：
        - link_resources: 用户上传的外部资源链接（传给 LLM）
        - binding_id: Engine 连接信息（Backend 内部使用）
        - iam_token: 用户 IAM token（供 Engine zero-check 使用）

        Returns:
            bot_info dict
        """
        bot = bot_service.get_bot(bot_id, owner_id)
        link_resources = self._collect_link_resources(bot_id, owner_id)

        # 从 bot.ext 中读取存储的 iam_token（由 router 层在触发 data_init 前写入）
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except (json.JSONDecodeError, TypeError):
                ext = {}
        iam_token = ext.get("iam_token", "")
        if iam_token:
            self._clear_stored_iam_token(bot_id, owner_id, bot_service)

        bot_info = {
            "binding_id": bot.get("binding_id"),
            "bot_name": bot.get("bot_name", ""),
            "bot_description": bot.get("bot_desc", ""),
            "link_resources": link_resources,
            "iam_token": iam_token,
        }

        logger.info(
            f"bot_id={bot_id} collect_bot_info link_count={len(link_resources)} "
            f"has_iam_token={bool(iam_token)}"
        )

        return bot_info

    @staticmethod
    def _clear_stored_iam_token(
        bot_id: str,
        owner_id: str,
        bot_service: Any,
    ) -> None:
        """读取后立即清除 DB 中临时保存的 IAM token。"""
        try:
            bot_service.update_bot_ext(
                bot_id, owner_id, {"iam_token": None}
            )
            logger.info(
                f"bot_id={bot_id} collect_bot_info iam_token_cleared"
            )
        except Exception as exc:
            logger.error(
                f"bot_id={bot_id} collect_bot_info "
                f"iam_token_clear_failed exc={exc}",
                exc_info=True,
            )
            raise

    def _collect_link_resources(self, bot_id: str, owner_id: str) -> list[dict]:
        """收集 Bot 关联的 LINK 类型资源。

        Returns:
            LINK 资源列表，每项包含 name、url、link_type、id
        """
        try:
            from agentclaw.community.core.resources.service import ResourceService

            service = ResourceService(bot_id=bot_id, repository=self._resource_repo)
            resources = service.list_resources(
                resource_type=None,
            )

            return [
                {
                    "name": r.name,
                    "url": r.attributes.get("url", ""),
                    "link_type": r.attributes.get("link_type", ""),
                    "id": str(r.id),
                }
                for r in resources
                if r.is_link
            ]
        except Exception as exc:
            logger.warning(f"bot_id={bot_id} collect_link_resources failed exc={exc}")
            return []

    # -- Engine WebSocket 通信 -------------------------------------------------

    def _get_engine_connection(self, bot_id: str, owner_id: str) -> dict:
        """通过 (bot_id, owner_id) 获取 Engine 连接信息。

        走 ``DeviceContextResolver``(全仓唯一 provider 解析点),由 resolver
        从 binding 表读 provider,委托对应 ``ConnInfoBuilder`` 算 ``conn_info``。
        替代旧的 ``device_service.get_device_connection_v2(binding_id, ...)``。

        Returns:
            conn_info dict, 字段语义不变(url、headers、use_proxy、sandbox_id、target、...)。
        """
        ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
        conn_info = ctx.conn_info

        logger.info(
            f"bot_id={bot_id} get_engine_connection resolved "
            f"provider={ctx.provider} binding_id={ctx.binding_id} "
            f"use_proxy={conn_info.get('use_proxy')} "
            f"sandbox_id={conn_info.get('sandbox_id')} url={conn_info.get('url')} "
            f"target={conn_info.get('target')} "
            f"header_keys={list((conn_info.get('headers') or {}).keys())}"
        )
        return conn_info

    def _send_to_engine(
        self,
        *,
        bot_id: str,
        owner_id: str,
        binding_id: int,
        link_resources: list[dict],
        bot_name: str = "",
        bot_description: str = "",
        iam_token: str = "",
    ) -> dict:
        """通过 Engine 发消息让 LLM 执行 data-init Skill。

        流程：
        1. HTTP 创建 session
        2. WS 握手 + chat.send
        3. 保持 WS 连接，读取事件流直到 LLM 完成（state=final/error/aborted）或超时
        4. 从 session messages 中提取 assistant 回复

        全部使用同步 I/O（requests + websocket-client），通过 asyncio.to_thread()
        在线程池中执行，避免阻塞 asyncio 事件循环。

        Args:
            iam_token: 用户 IAM token，用于通过 Engine zero-check 鉴权

        Returns:
            解析后的 LLM 结果 dict，包含 summary、raw_data、fetch_errors
        """
        import base64
        import time
        import requests as sync_requests
        import websocket as ws_client

        logger.info(
            f"bot_id={bot_id} send_to_engine entry "
            f"binding_id={binding_id} link_count={len(link_resources)}"
        )

        # 防御性等待：report_device_status(SUCCEEDED) 时 Gateway 可能还在启动最后几秒，
        # 配合下方的 Gateway 重启检测 + 删脏 session + 重试机制，形成双重保险
        time.sleep(3)

        # 1. 动态获取 Engine 连接信息 — 走 resolver(bot_id, owner_id)
        conn_info = self._get_engine_connection(bot_id, owner_id)
        base_url = conn_info.get("url", "")

        headers = conn_info.get("headers", {})
        logger.info(
            f"bot_id={bot_id} send_to_engine connection_ready "
            f"base_url={base_url} use_proxy={conn_info.get('use_proxy')}"
        )

        session_url = f"{base_url}/api/sessions"
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/api/openclaw/ws"

        # 2. 创建 Engine Session（同步 HTTP）
        _t_sess = time.time()
        try:
            resp = sync_requests.post(
                session_url,
                json={
                    "title": "Bot 初始化配置",
                    "user_id": owner_id,
                    "agent_id": "default",
                    "engine": "openclaw",
                },
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except sync_requests.exceptions.RequestException as exc:
            logger.error(
                f"bot_id={bot_id} send_to_engine session_create_failed "
                f"url={session_url} exc={exc} cost_ms={(time.time() - _t_sess) * 1000:.0f}"
            )
            raise RuntimeError(
                f"bot_id={bot_id} Failed to create Engine session, url={session_url}, exc={exc}"
            ) from exc
        except (ValueError, KeyError) as exc:
            logger.error(
                f"bot_id={bot_id} send_to_engine session_response_invalid "
                f"status={resp.status_code} body={resp.text[:300]} exc={exc}"
            )
            raise RuntimeError(
                f"bot_id={bot_id} Invalid response from Engine session API, "
                f"status={resp.status_code}, body={resp.text[:300]}, exc={exc}"
            ) from exc
        session_key = data.get("data", {}).get("id") or data.get("id", "")
        if not session_key:
            logger.error(
                f"bot_id={bot_id} send_to_engine session_key_missing "
                f"resp={data} cost_ms={(time.time() - _t_sess) * 1000:.0f}"
            )
            raise RuntimeError(f"bot_id={bot_id} Failed to create Engine session, resp={data}")
        logger.info(
            f"bot_id={bot_id} send_to_engine session_created "
            f"session_key={session_key} http_status={resp.status_code} "
            f"cost_ms={(time.time() - _t_sess) * 1000:.0f}"
        )

        # 3. WebSocket 握手 + chat.send + 等待 LLM 完成（同步 websocket-client）
        # 关键修复：不在 chat.send accepted 后立即关闭 WS，
        # 而是保持连接读取事件流，确保 Engine 的 _stream_chat_events 能完成 LLM 处理。
        # 否则 Engine 侧 WebSocketDisconnect 会导致 LLM 响应丢失。
        _t_ws_conn = time.time()
        try:
            ws = ws_client.create_connection(ws_url, timeout=15, header=headers)
        except Exception as ws_conn_exc:
            logger.error(
                f"bot_id={bot_id} send_to_engine ws_connect_failed "
                f"url={ws_url} exc={ws_conn_exc} "
                f"cost_ms={(time.time() - _t_ws_conn) * 1000:.0f}"
            )
            raise
        logger.info(
            f"bot_id={bot_id} send_to_engine ws_tcp_connected "
            f"url={ws_url} cost_ms={(time.time() - _t_ws_conn) * 1000:.0f}"
        )
        try:
            # 3a. Protocol v3 握手
            _t_hs = time.time()
            connect_req_id = uuid.uuid4().hex[:8]
            ws.send(json.dumps({
                "type": "req",
                "id": connect_req_id,
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": "data-init-service",
                        "version": "1.0",
                        "platform": "server",
                        "mode": "headless",
                    },
                    "role": "operator",
                    "user_id": owner_id,
                },
            }))
            hello_raw = ws.recv()
            hello_data = json.loads(hello_raw)
            if not hello_data.get("ok"):
                logger.error(
                    f"bot_id={bot_id} send_to_engine handshake_failed "
                    f"req_id={connect_req_id} resp={hello_data} "
                    f"cost_ms={(time.time() - _t_hs) * 1000:.0f}"
                )
                raise RuntimeError(f"WebSocket handshake failed, bot_id={bot_id}, resp={hello_data}")
            logger.info(
                f"bot_id={bot_id} send_to_engine handshake_ok "
                f"req_id={connect_req_id} hello_payload_keys={list((hello_data.get('payload') or {}).keys())} "
                f"cost_ms={(time.time() - _t_hs) * 1000:.0f}"
            )

            # 3b. 发送 chat.send -- 传 bot 信息 + link_resources 给 LLM
            context_payload = {
                "bot_id": bot_id,
                "link_resources": link_resources,
            }
            if bot_name:
                context_payload["bot_name"] = bot_name
            if bot_description:
                context_payload["bot_description"] = bot_description
            llm_context = json.dumps(context_payload, ensure_ascii=False)
            message = (
                f"请先读取 {self._skill_md_path} 文件，然后严格按照其中的指令执行数据初始化流程。\n\n"
                f"<system_context>\n"
                f"bot_info: {llm_context}\n"
                f"</system_context>"
            )
            chat_req_id = uuid.uuid4().hex[:8]
            idempotency_key = uuid.uuid4().hex
            chat_send_params = {
                "sessionKey": session_key,
                "message": message,
                "idempotencyKey": idempotency_key,
            }
            # 携带 IAM token 用于通过 Engine zero-check 鉴权
            if iam_token:
                chat_send_params["x-iam-token"] = iam_token
            chat_req = {
                "type": "req",
                "id": chat_req_id,
                "method": "chat.send",
                "params": chat_send_params,
            }
            _t_send_msg = time.time()
            logger.info(
                f"bot_id={bot_id} send_to_engine chat_send_sending "
                f"req_id={chat_req_id} session_key={session_key} "
                f"msg_len={len(message)} idempotency_key={idempotency_key} "
                f"has_iam_token={bool(iam_token)}"
            )
            ws.send(json.dumps(chat_req))

            # 3c. 等待 accepted 响应
            accepted_raw = ws.recv()
            accepted_data = json.loads(accepted_raw)
            logger.info(
                f"bot_id={bot_id} send_to_engine chat_send_accepted "
                f"req_id={chat_req_id} ok={accepted_data.get('ok')} "
                f"payload={accepted_data.get('payload')} "
                f"error={accepted_data.get('error')} "
                f"cost_ms={(time.time() - _t_send_msg) * 1000:.0f}"
            )
            if not accepted_data.get("ok"):
                raise RuntimeError(f"chat.send rejected, bot_id={bot_id}, resp={accepted_data}")

            # 3d. 保持 WS 连接，读取事件流直到 LLM 完成
            # Engine 通过 WS 推送 chat 事件，直到 state=final/error/aborted 表示完成
            # 这样 Engine 侧的 _stream_chat_events 能完整消费 chat_stream，
            # 确保 LLM 响应被写入 session store
            ws.settimeout(_WS_EVENT_TIMEOUT)
            _t_stream_start = time.time()
            ws_event_count = 0
            last_state = ""
            state_counts: dict = {}
            first_event_ms = -1.0
            _t_last_content = time.time()  # 最后收到非 tick 事件的时间
            try:
                while True:
                    try:
                        raw = ws.recv()
                        if not raw:
                            logger.info(
                                f"bot_id={bot_id} send_to_engine ws_closed_by_server "
                                f"events={ws_event_count} last_state={last_state} "
                                f"state_counts={state_counts} "
                                f"stream_ms={(time.time() - _t_stream_start) * 1000:.0f}"
                            )
                            break
                        event = json.loads(raw)
                        ws_event_count += 1
                        if first_event_ms < 0:
                            first_event_ms = (time.time() - _t_stream_start) * 1000
                            logger.info(
                                f"bot_id={bot_id} send_to_engine first_ws_event "
                                f"wait_ms={first_event_ms:.0f} event_type={event.get('type')} "
                                f"raw_preview={raw[:300]}"
                            )

                        event_type = event.get("type", "")
                        event_name = event.get("event", "")
                        payload = (event.get("payload", {})
                                   or event.get("data", {}))
                        state = (payload.get("state", "")
                                 or payload.get("stream", ""))

                        # 无内容超时检测：连续只收到 tick 时提前退出
                        if event_name == "tick":
                            elapsed = (time.time()
                                       - _t_last_content)
                            if elapsed > _WS_NO_CONTENT_TIMEOUT:
                                stream_ms = (
                                    (time.time() - _t_stream_start)
                                    * 1000
                                )
                                logger.warning(
                                    f"bot_id={bot_id} "
                                    f"send_to_engine "
                                    f"ws_no_content_timeout "
                                    f"timeout_s="
                                    f"{_WS_NO_CONTENT_TIMEOUT} "
                                    f"events={ws_event_count} "
                                    f"last_state={last_state} "
                                    f"stream_ms={stream_ms:.0f}"
                                )
                                break
                        else:
                            _t_last_content = time.time()

                        if state:
                            state_counts[state] = state_counts.get(state, 0) + 1
                        if state and state != last_state:
                            last_state = state
                            logger.info(
                                f"bot_id={bot_id} send_to_engine ws_state_transition "
                                f"events={ws_event_count} state={state} "
                                f"event_type={event_type} "
                                f"elapsed_ms={(time.time() - _t_stream_start) * 1000:.0f}"
                            )

                        # 检查终止状态
                        if state in ("final", "error", "aborted"):
                            logger.info(
                                f"bot_id={bot_id} send_to_engine ws_llm_finished "
                                f"state={state} events={ws_event_count} "
                                f"state_counts={state_counts} "
                                f"stream_ms={(time.time() - _t_stream_start) * 1000:.0f}"
                            )
                            break

                        # 检查 WS 响应中的 error
                        if event_type == "res" and not event.get("ok"):
                            logger.warning(
                                f"bot_id={bot_id} send_to_engine ws_error_response "
                                f"events={ws_event_count} raw={raw[:500]}"
                            )
                            break

                    except ws_client.WebSocketTimeoutException:
                        logger.warning(
                            f"bot_id={bot_id} send_to_engine ws_recv_timeout "
                            f"timeout_s={_WS_EVENT_TIMEOUT} events={ws_event_count} "
                            f"last_state={last_state} state_counts={state_counts} "
                            f"stream_ms={(time.time() - _t_stream_start) * 1000:.0f}"
                        )
                        break
            except Exception as ws_exc:
                logger.warning(
                    f"bot_id={bot_id} send_to_engine ws_recv_error "
                    f"exc={ws_exc} events={ws_event_count} "
                    f"state_counts={state_counts} "
                    f"stream_ms={(time.time() - _t_stream_start) * 1000:.0f}",
                    exc_info=True,
                )

            logger.info(
                f"bot_id={bot_id} send_to_engine ws_stream_ended "
                f"events={ws_event_count} last_state={last_state} "
                f"state_counts={state_counts} "
                f"stream_ms={(time.time() - _t_stream_start) * 1000:.0f}"
            )
        finally:
            ws.close()
            logger.info(
                f"bot_id={bot_id} send_to_engine ws_closed "
                f"total_ws_ms={(time.time() - _t_ws_conn) * 1000:.0f}"
            )

        # 4. 从 session messages 获取 LLM 回复（同步 HTTP）
        logger.info(f"bot_id={bot_id} send_to_engine fetching_llm_response")
        encoded_key = base64.b64encode(session_key.encode()).decode()
        messages_url = f"{base_url}/api/sessions/{encoded_key}/messages"

        poll_interval = 5
        poll_deadline = time.time() + 30  # WS 已等完成，最多再轮询 30s
        last_message_count = 0
        _gateway_restarting = False

        while time.time() < poll_deadline:
            try:
                poll_resp = sync_requests.get(
                    messages_url,
                    headers=headers,
                    params={"limit": 999},
                    timeout=10,
                )
                poll_data = poll_resp.json()

                if not poll_data.get("success"):
                    logger.warning(
                        f"bot_id={bot_id} send_to_engine poll_api_error resp={poll_data}"
                    )
                    time.sleep(poll_interval)
                    continue

                messages = poll_data.get("data", [])
                current_count = len(messages)

                if current_count != last_message_count:
                    logger.info(
                        f"bot_id={bot_id} send_to_engine messages_updated "
                        f"count={current_count} delta={current_count - last_message_count}"
                    )
                    last_message_count = current_count

                # 临时方案：检测 Gateway 重启注入的错误消息
                # 当 report_device_status(SUCCEEDED) 时
                # Gateway 可能还未完全启动，
                # 创建的 session 会被 Engine 注入
                # gateway-injected 错误消息
                for msg in messages:
                    content = msg.get("content", [])
                    text_to_check = ""
                    if isinstance(content, str):
                        text_to_check = content
                    elif isinstance(content, list):
                        for block in content:
                            if (isinstance(block, dict)
                                    and block.get("type")
                                    == "text"):
                                text_to_check += (
                                    block.get("text", "")
                                )
                            elif isinstance(block, str):
                                text_to_check += block
                    if _GATEWAY_RESTART_MARKER in text_to_check:
                        _gateway_restarting = True
                        break
                if _gateway_restarting:
                    logger.warning(
                        f"bot_id={bot_id} "
                        f"send_to_engine "
                        f"gateway_restarting_detected "
                        f"session_key="
                        f"{session_key[:30]}... "
                        f"messages={len(messages)}"
                    )
                    # 删除包含 gateway-injected 消息的脏 session
                    try:
                        delete_url = (
                            f"{base_url}/api/sessions/"
                            f"{encoded_key}"
                        )
                        sync_requests.delete(
                            delete_url,
                            headers=headers,
                            timeout=10,
                        )
                        logger.info(
                            f"bot_id={bot_id} "
                            f"send_to_engine "
                            f"gateway_session_deleted "
                            f"session_key="
                            f"{session_key[:30]}..."
                        )
                    except Exception as del_exc:
                        logger.warning(
                            f"bot_id={bot_id} "
                            f"send_to_engine "
                            f"gateway_session_delete_"
                            f"failed exc={del_exc}"
                        )
                    # 临时方案：外层 execute_with_retry 会重试
                    break

                assistant_text = self._extract_assistant_response(messages)
                if assistant_text:
                    logger.info(
                        f"bot_id={bot_id} send_to_engine got_response len={len(assistant_text)}"
                    )
                    result = self._parse_llm_result(assistant_text, bot_id=bot_id)
                    logger.info(
                        f"bot_id={bot_id} send_to_engine parsed_result "
                        f"capability={result.get('summary', {}).get('capability', '')[:80]} "
                        f"fetch_errors={result.get('fetch_errors', [])}"
                    )
                    return result

            except Exception as exc:
                logger.warning(f"bot_id={bot_id} send_to_engine poll_error exc={exc}")

            time.sleep(poll_interval)

        if _gateway_restarting:
            raise RuntimeError(
                f"Gateway not ready, session deleted. bot_id={bot_id}"
            )

        raise RuntimeError(
            f"bot_id={bot_id} Timeout waiting for LLM response after WS stream ended, "
            f"ws_events={ws_event_count}, last_state={last_state}"
        )

    @staticmethod
    def _extract_assistant_response(messages: list) -> str:
        """从消息列表中提取最后一条完整的 assistant 回复文本。

        消息格式参考 Engine session_router 的 _message_to_dict 输出。
        只有当 assistant 消息看起来已经完成时才返回（包含结束标记或足够长）。
        """
        if not messages:
            return ""

        # 从后往前找最后一条 assistant 消息
        for msg in reversed(messages):
            role = msg.get("role", "")
            if role != "assistant":
                continue

            # 提取文本内容
            text_parts = []
            content = msg.get("content", [])
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)

            full_text = "\n".join(text_parts)

            # 检查是否包含 data-init 结果标记（表示 LLM 已完成）
            if _RESULT_BEGIN in full_text and _RESULT_END in full_text:
                return full_text

        return ""

    @staticmethod
    def _parse_llm_result(llm_text: str, bot_id: str = "") -> dict:
        """从 LLM 回复中解析 DATA_INIT_RESULT_BEGIN/END 标记之间的 JSON。

        Returns:
            解析后的 dict，包含 summary、raw_data、fetch_errors
        """
        begin_idx = llm_text.find(_RESULT_BEGIN)
        end_idx = llm_text.find(_RESULT_END)
        match = (begin_idx >= 0 and end_idx > begin_idx)

        if match:
            json_str = llm_text[begin_idx + len(_RESULT_BEGIN):end_idx].strip()
        else:
            json_str = None

        if not json_str:
            logger.warning(
                f"bot_id={bot_id} parse_llm_result no_markers_found "
                f"response_preview={llm_text[:300]}"
            )
            return {"summary": {}, "raw_data": {}, "fetch_errors": ["LLM did not return structured result"]}

        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error(f"bot_id={bot_id} parse_llm_result invalid_json exc={exc}")
            return {"summary": {}, "raw_data": {}, "fetch_errors": [f"Invalid JSON: {exc}"]}

        if "summary" not in result:
            result["summary"] = {}
        if "raw_data" not in result:
            result["raw_data"] = {}
        if "fetch_errors" not in result:
            result["fetch_errors"] = []

        return result
