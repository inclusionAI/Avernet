"""Binding 解析服务 — bot_id → BotBindingInfo。

原 ``_bot_run_utils.resolve_binding / resolve_caller_binding`` 以函数参数逐层
传递 ``BotServicePlugin``；现收敛为 ``BotBindingResolver``：插件经 DI 注入本类，
BotRunner / BotRunRequestExecutor 等外部只依赖本对象做 binding 解析。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import BotBindingInfo
from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.logger import get_logger

from ._bot_run_utils import (
    binding_data_to_info,
    build_caller_binding,
    extract_lifecycle_stage,
    parse_bot_id,
)

if TYPE_CHECKING:
    from secbaas.community.spi.bot_service import BotServicePlugin

logger = get_logger("core-bot-run")


class BotBindingResolver:
    """bot_id → BotBindingInfo 解析（runner 入队时与 worker 执行时共用）。

    ``resolve_binding`` 只做正常模式解析（既有发布设备）；caller 模式的
    容器拉起由 :meth:`resolve_caller_binding` 单独承担——仅 runner 的后台
    dispatch 调用（拉起可达分钟级，不在 HTTP 同步链路上等待），worker 侧
    则直接用 queue meta 里的 sandbox_id 组装 binding，不再二次解析。
    """

    def __init__(self, bot_service_plugin: BotServicePlugin) -> None:
        self._bot_service_plugin = bot_service_plugin

    async def resolve_binding(
        self,
        *,
        bot_id: str,
        metadata: dict[str, Any],
        lifecycle_stage: str | None = None,
    ) -> BotBindingInfo | None:
        """按 (bot, owner) + lifecycle_stage 解析既有发布设备；NOT_FOUND 返回 None。

        纯正常模式解析；caller 模式不经过本方法（容器拉起见
        :meth:`resolve_caller_binding`，worker 复用见 queue meta 的
        ``caller_sandbox_id``）。

        lifecycle_stage 显式传入时覆盖 metadata 提取；eval 阶段从
        metadata 顶层提取 ``default_tag`` 透传（按 tag 路由评测 binding），
        其余阶段恒为 None。
        """
        stage = lifecycle_stage or extract_lifecycle_stage(metadata)
        default_tag: str | None = None
        if stage == "eval":
            tag = metadata.get("default_tag")
            if tag:
                default_tag = str(tag)
        real_bot_id, entity_id = parse_bot_id(bot_id)
        if not real_bot_id:
            return None
        try:
            data = await self._bot_service_plugin.get_binding(
                bot_id=real_bot_id,
                owner_id=entity_id or "",
                stage=stage,
                default_tag=default_tag,
            )
        except PaasError as e:
            if e.code == ErrorCode.NOT_FOUND:
                logger.warning(
                    "[resolve_binding] Bot binding unavailable: bot_id=%s, "
                    "lifecycle_stage=%s, error=%s",
                    bot_id,
                    lifecycle_stage,
                    e,
                )
                return None
            raise
        return binding_data_to_info(data)

    async def resolve_caller_binding(
        self,
        *,
        bot_id: str,
        metadata: dict[str, Any],
    ) -> BotBindingInfo:
        """caller 模式 binding：依赖 caller-connection 按 (bot_id, owner_id, user_id) 拉起容器。

        与 ``binding_data_to_info``（按 (bot, owner) 解析既有发布设备）不同，caller 模式
        每次现拉容器，用 ``device_provider="caller"`` 标记，使 ``BotServiceSelector`` 路由到
        ``CallerBotService``，并把返回的 sandbox_id 作为连接目标（不参与 device affinity 选设备）。

        拉起含 need_poll 轮询（分钟级），只应由 runner 的后台 dispatch 调用；
        metadata 的 ``cookie``（IAM 凭据）只存在于内存请求链路，不落库。
        Principal 由 bot_service 插件用共享密钥自签。
        """
        real_bot_id, entity_id = parse_bot_id(bot_id)
        user_id = str(metadata.get("user_id") or "")
        cookie = str(metadata.get("cookie") or "")
        sandbox_id = await self._bot_service_plugin.get_caller_connection(
            bot_id=real_bot_id,
            owner_id=entity_id,
            user_id=user_id,
            cookie=f"IAM_TOKEN={cookie}",
        )
        logger.info(
            "[resolve_caller_binding] bot_id=%s owner_id=%s user_id=%s sandbox_id=%s",
            real_bot_id,
            entity_id,
            user_id,
            sandbox_id,
        )
        return build_caller_binding(bot_id, sandbox_id)
