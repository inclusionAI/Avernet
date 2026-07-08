"""Cron auto-setup service — 自动为 is_hosted_24x7 的应用 Coding Bot 创建定时任务。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional
from injector import inject

from agentclaw.community.core.bot_management.repository.template_repository_protocol import TemplateRepository
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.log import get_logger

logger = get_logger()

# 定时任务默认配置
DEFAULT_CRON_SCHEDULE = "0 10,18 * * *"  # 每天上午10点、下午18点各一次
DEFAULT_CRON_TIMEZONE = "Asia/Shanghai"
DEFAULT_CRON_TIMEOUT_SECS = 86400  # 24小时
DEFAULT_CRON_MODEL = None  # 使用引擎默认模型


def _build_cron_command(
    dima_space_id: str,
    user_id: str = "",
    agent_id: str = "",
    message: str = "",
    kind: str = "autoInitiate",
    workflow: str = "",
    append_message: str = "",
) -> str:
    """根据 dima_space_id 等参数构建定时任务命令。

    Args:
        dima_space_id: DIMA 空间 ID
        user_id: 用户 ID
        agent_id: Agent ID (Bot ID)
        message: 附加消息
        kind: 任务类型 (autoInitiate 或 agentTurn)
        workflow: Devflow 工作流名称
        append_message: 补充说明，拼接在发起消息末尾
    """
    prefix = f"查询dima空间{dima_space_id}的待开发需求，开启7*24小时自动研发"
    parts = [
        f"space:{dima_space_id}",
        f"user:{user_id}",
        f"agent:{agent_id}",
        f"kind:{kind}",  # 嵌入 kind 字段，避免依赖 relay 存储
    ]
    if workflow:
        parts.append(f"workflow:{workflow}")
    if message:
        parts.append(f"message:{message}")
    if append_message:
        parts.append(f"append_message:{append_message}")
    return f"{prefix}|{'|'.join(parts)}"


def _is_hosted_24x7(template_config: Dict[str, Any]) -> bool:
    """检查 template_config 中 is_hosted_24x7 是否为 1（true）"""
    value = template_config.get("is_hosted_24x7")
    return type(value) is int and value == 1


def _get_trigger_frequency(template_config: Dict[str, Any]) -> str:
    """获取触发频率，默认 daily"""
    return template_config.get("trigger_frequency", "daily")


def _frequency_to_cron(frequency: str) -> str:
    """将 trigger_frequency 转换为 cron 表达式"""
    mapping = {
        "hourly": "0 * * * *",          # 每小时
        "daily": "0 10,18 * * *",       # 每天10点和18点
        "weekly": "0 10,18 * * 1",      # 每周一10点和18点
    }
    return mapping.get(frequency, "0 10,18 * * *")


def _build_cron_name(bot_name: str, bot_id: str) -> str:
    """构建定时任务名称，保证唯一性"""
    return f"7*24小时自动生码_{bot_name}_{bot_id}"


def _parse_cron_command(command: str) -> Dict[str, str]:
    """解析定时任务命令字符串，返回字段字典。

    命令格式: 前缀|space:{id}|user:{id}|agent:{id}|kind:{kind}|workflow:{name}|...
    """
    result: Dict[str, str] = {}
    parts = command.split("|")
    for part in parts[1:]:
        if ":" in part:
            key, _, value = part.partition(":")
            result[key] = value
    return result


def _replace_workflow_in_command(command: str, new_workflow: str) -> str:
    """直接在 cron command 字符串中替换 workflow 的值，不重建命令。

    优势：未来新增字段时无需修改此函数，其他字段不会被丢失。

    Args:
        command: 原始 cron command 字符串
        new_workflow: 新的工作流名称

    Returns:
        替换后的 command 字符串
    """
    if "workflow:" in command:
        # 替换已有的 workflow 值（workflow:后面到下一个 | 或字符串末尾的整段内容）
        return re.sub(r"(workflow:)[^|]*", rf"\1{new_workflow}", command)
    else:
        # 原命令无 workflow 字段，追加在末尾
        return f"{command}|workflow:{new_workflow}"


class CronAutoSetupError(Exception):
    """自动创建定时任务失败"""
    pass


class CronAutoSetupService:
    """自动为 is_hosted_24x7 的 Bot 创建定时任务的服务。

    依赖注入方式使用:
        - 构造函数注入 TemplateRepository 和 CronRelayService
        - 通过 BotManagementModule 注册为 singleton
    """

    @inject
    def __init__(
        self,
        template_repository: TemplateRepository,
        cron_relay_service: CronRelayService,
    ) -> None:
        self._template_repo = template_repository
        self._cron_relay = cron_relay_service

    async def auto_setup_cron_for_bot(
        self,
        bot_id: str,
        owner_id: str,
        nick_name: str,
    ) -> Optional[Dict[str, Any]]:
        """为 Bot 自动创建 7×24 托管定时任务。

        判断条件（必须同时满足）：
        1. active_engine 为 aicoding 或其别名
        2. template_type 为 "applicationCoding"
        3. template_config.is_hosted_24x7 == 1

        Args:
            bot_id: Bot ID
            owner_id: Bot 所有者 ID
            nick_name: Bot 所有者花名

        Returns:
            创建成功返回 cron 任务数据，跳过或失败返回 None

        Raises:
            CronAutoSetupError: 创建过程中出现不可恢复的错误
        """
        # 1. 读取 template_config
        try:
            template = self._template_repo.get_by_bot_id(bot_id)
            logger.info(f"[auto_setup_cron] Template query result for bot {bot_id}: template={'found' if template else 'not found'}")
        except Exception as e:
            logger.warning(f"[auto_setup_cron] Failed to get template for bot {bot_id}: {e}", exc_info=True)
            return None

        if not template:
            logger.info(f"[auto_setup_cron] No template found for bot {bot_id}, skipping")
            return None

        ext = template.get("ext") or {}
        if not isinstance(ext, dict):
            logger.warning(f"[auto_setup_cron] Template ext is not dict for bot {bot_id} (type={type(ext).__name__}), skipping")
            return None

        # 2. 检查 is_hosted_24x7
        is_hosted = _is_hosted_24x7(ext)
        logger.info(f"[auto_setup_cron] Bot {bot_id} template ext keys: {list(ext.keys()) if isinstance(ext, dict) else 'N/A'}, is_hosted_24x7={ext.get('is_hosted_24x7')}")
        if not is_hosted:
            logger.info(f"[auto_setup_cron] Bot {bot_id} is_hosted_24x7 is not 1, skipping")
            return None

        logger.info(f"[auto_setup_cron] Bot {bot_id} has is_hosted_24x7=1, will create cron task")

        # 3. 获取 dima_space_id
        dima_space_id = ext.get("dima_space_id")
        if not dima_space_id:
            logger.warning(
                f"[auto_setup_cron] Bot {bot_id} has no dima_space_id in template_config, "
                f"skipping cron setup"
            )
            return None

        # 4. 构建 cron 任务参数
        bot_name = template.get("name", "")
        cron_name = _build_cron_name(bot_name, bot_id)
        trigger_frequency = _get_trigger_frequency(ext)
        cron_expr = _frequency_to_cron(trigger_frequency)

        # 提取 devflow_workflow 名称
        devflow_workflow = ext.get("devflow_workflow", "")
        workflow_name = ""
        if isinstance(devflow_workflow, dict):
            workflow_name = devflow_workflow.get("name", "")
        elif isinstance(devflow_workflow, str):
            workflow_name = devflow_workflow

        # 提取补充说明
        append_message = ext.get("append_message", "")

        command = _build_cron_command(
            dima_space_id=dima_space_id,
            user_id=owner_id,
            agent_id=bot_id,
            workflow=workflow_name,
            append_message=append_message,
        )

        # 5. 检查是否已存在同名任务（幂等性）
        try:
            existing_crons = await self._cron_relay.list_all_crons(
                user_id=owner_id,
                nick_name=nick_name,
                bot_id=bot_id,
            )
            if existing_crons.get("success"):
                for cron in existing_crons.get("data", []):
                    if cron.get("name") == cron_name:
                        logger.info(
                            f"[auto_setup_cron] Cron task '{cron_name}' already exists for bot {bot_id}, skipping"
                        )
                        return None
        except Exception as e:
            logger.warning(f"[auto_setup_cron] Failed to check existing crons for bot {bot_id}: {e}")

        # 6. 创建 cron 任务
        # kind 传递给引擎，确保 payload.kind 被正确设置为 autoInitiate
        adapter_body = {
            "name": cron_name,
            "schedule": cron_expr,
            "command": command,
            "timezone": DEFAULT_CRON_TIMEZONE,
            "enabled": True,
            "timeout_secs": DEFAULT_CRON_TIMEOUT_SECS,
            "kind": "autoInitiate",  # 显式传递 kind，供引擎设置 payload.kind
        }
        if DEFAULT_CRON_MODEL:
            adapter_body["model"] = DEFAULT_CRON_MODEL

        try:
            result = await self._cron_relay.forward_request(
                bot_id=bot_id,
                user_id=owner_id,
                nick_name=nick_name,
                method="POST",
                path="/api/cron",
                body=adapter_body,
            )
            logger.info(
                f"[auto_setup_cron] Successfully created cron task '{cron_name}' for bot {bot_id}, "
                f"result: {result}"
            )
            return result
        except Exception as e:
            logger.error(
                f"[auto_setup_cron] Failed to create cron task for bot {bot_id}: {e}",
                exc_info=True,
            )
            raise CronAutoSetupError(f"Auto cron setup failed for bot {bot_id}: {e}")

    async def update_auto_initiate_workflow(
        self,
        bot_id: str,
        owner_id: str,
        nick_name: str,
        new_workflow_name: str,
    ) -> bool:
        """当 devflow_workflow 发生变化时，更新已有 autoInitiate 定时任务的 workflow 字段。

        使用正则直接替换 workflow 值，不重建 command ——
        这样未来新增字段时无需同步修改此方法，所有已有字段原样保留。

        Args:
            bot_id: Bot ID
            owner_id: Bot 所有者 ID
            nick_name: Bot 所有者花名
            new_workflow_name: 新的工作流名称

        Returns:
            True 表示更新成功，False 表示未找到任务或跳过
        """
        try:
            existing_crons = await self._cron_relay.list_all_crons(
                user_id=owner_id,
                nick_name=nick_name,
                bot_id=bot_id,
            )
        except Exception as e:
            logger.warning(
                f"[update_auto_initiate_workflow] Failed to list crons for bot {bot_id}: {e}"
            )
            return False

        if not existing_crons.get("success"):
            return False

        for cron in existing_crons.get("data", []):
            payload = cron.get("payload", {})
            if not isinstance(payload, dict):
                continue
            message = payload.get("message") or payload.get("prompt") or ""
            if not isinstance(message, str) or "kind:autoInitiate" not in message:
                continue

            parsed = _parse_cron_command(message)
            old_workflow = parsed.get("workflow", "")
            if old_workflow == new_workflow_name:
                logger.info(
                    f"[update_auto_initiate_workflow] Workflow unchanged for bot {bot_id}, skipping"
                )
                return False

            new_command = _replace_workflow_in_command(message, new_workflow_name)

            task_id = cron.get("id") or cron.get("task_id")
            if not task_id:
                logger.warning(
                    f"[update_auto_initiate_workflow] No task_id found for bot {bot_id}"
                )
                return False

            try:
                await self._cron_relay.forward_request(
                    bot_id=bot_id,
                    user_id=owner_id,
                    nick_name=nick_name,
                    method="PUT",
                    path=f"/api/cron/{task_id}",
                    body={"command": new_command},
                )
                logger.info(
                    f"[update_auto_initiate_workflow] Updated workflow from '{old_workflow}' "
                    f"to '{new_workflow_name}' for bot {bot_id}, task {task_id}"
                )
                return True
            except Exception as e:
                logger.error(
                    f"[update_auto_initiate_workflow] Failed to update cron task for bot {bot_id}: {e}",
                    exc_info=True,
                )
                return False

        logger.info(
            f"[update_auto_initiate_workflow] No autoInitiate cron found for bot {bot_id}"
        )
        return False
