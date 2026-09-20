"""Aicoding 引擎特有：托管工作空间的判定与开通逻辑。

`provision_hosted_workspace`（创建期）/`ensure_hosted_workspace`（补救期）在内部
判定是否需要托管工作空间，BotService 只调这两个入口，引擎策略内部自行决定是否
托管并完成开通/回滚，不暴露单独的 needs 判定入口；非 aicoding 引擎（默认策略）
只继承默认实现即可，无需关注额外概念。
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.log import get_logger

from ..provisioning import (
    BotProvisioningContext,
    HostedWorkspaceProvisioningError,
    hosted_workspace_not_eligible_error,
)
from ...services.aicoding.dima_workspace_capability import (
    has_dima_workspace_enabled,
)

logger = get_logger(__name__)


class AicodingHostedWorkspaceMixin:
    """托管工作空间：applicationCoding，或显式开启 workspace 托管能力时需要。"""

    def _needs_hosted_workspace(self, ctx: BotProvisioningContext) -> bool:
        return ctx.template_type == "applicationCoding" or has_dima_workspace_enabled(
            ctx.template_config
        )

    def provision_hosted_workspace(
        self,
        ctx: BotProvisioningContext,
        *,
        bot_name: str,
        workspace_hosting_provider: Any,
    ) -> Optional[str]:
        """创建期为新 bot 开通托管工作空间，返回 workspace id。

        无需托管时返回 None（当前 bot 非编码场景直接跳过）。开通底层调用自身抛错
        时原样冒泡（由 BotService 统一回滚报错）；仅在"需要开通但未拿到 id"时单独
        标示，以便上层区分回滚措辞。``workspace_hosting_provider`` 是零参可调用：
        仅当确实需要开通时才调用，因此非编码 bot 不会触发 workspace hosting 服务的
        依赖连通性校验。
        """
        if not self._needs_hosted_workspace(ctx):
            return None
        workspace_hosting_service = workspace_hosting_provider()
        workspace_id = workspace_hosting_service.create_workspace_for_bot(
            staff_id=ctx.owner_id,
            bot_id=ctx.bot_id,
            bot_name=bot_name,
            template_config=ctx.template_config,
        )
        if not workspace_id:
            logger.error(
                "[bot_service.create_bot] hosted workspace creation returned "
                "no id bot_id=%s",
                ctx.bot_id,
            )
            raise HostedWorkspaceProvisioningError(
                "applicationCoding workspace creation returned no id"
            )
        logger.info(
            "[bot_service.create_bot] Created hosted workspace %s for bot %s",
            workspace_id,
            ctx.bot_id,
        )
        return workspace_id

    def ensure_hosted_workspace(
        self,
        ctx: BotProvisioningContext,
        *,
        bot_name: str,
        template_config: Any,
        template_service: Any,
        workspace_hosting_provider: Any,
    ) -> Optional[str]:
        """补救期为已存在 bot 确保托管工作空间存在（幂等），返回 workspace id。

        幂等：``template_config`` 已有 ``dima_space_id`` 则直接返回不重复开通。
        未开启托管能力时抛错（资格校验先于幂等短路）。创建失败返回 None。
        ``workspace_hosting_provider`` 是零参可调用，仅进入真正开通步骤时才调用。
        """
        if not self._needs_hosted_workspace(ctx):
            raise hosted_workspace_not_eligible_error(ctx)
        existing_id = (template_config or {}).get("dima_space_id")
        if existing_id:
            logger.info(
                "[hosted_workspace.ensure] bot %s already has dima_space_id=%s, skip",
                ctx.bot_id,
                existing_id,
            )
            return existing_id
        workspace_hosting_service = workspace_hosting_provider()
        workspace_id = workspace_hosting_service.create_workspace_for_bot(
            staff_id=ctx.owner_id,
            bot_id=ctx.bot_id,
            bot_name=bot_name,
            template_config=template_config,
            raise_on_failure=True,
        )
        if not workspace_id:
            logger.warning(
                "[hosted_workspace.ensure] hosted workspace create failed for bot %s",
                ctx.bot_id,
            )
            return None
        # template_config 已被 create_workspace_for_bot inline 写入 dima_space_id；
        # 用 create_or_update 兜底首次（template 记录可能尚不存在）
        try:
            template_service.create_or_update_template(
                bot_id=ctx.bot_id,
                template_config=template_config,
                template_type=ctx.template_type,
            )
            logger.info(
                "[hosted_workspace.ensure] persisted dima_space_id=%s for bot %s",
                workspace_id,
                ctx.bot_id,
            )
        except Exception as e:
            logger.error(
                "[hosted_workspace.ensure] failed to persist template_config for bot %s: %s",
                ctx.bot_id,
                e,
                exc_info=True,
            )
            # workspace 已经创建成功，持久化失败仍返回 ID 让前端可重试持久化逻辑
            # （下次调用会因 dima_space_id 不在持久化记录中而重新进入此分支）
        return workspace_id
