"""AICoding 托管工作空间的"补救"入口（ensure）。

创建期失败后，前端通过该兜底服务为已存在的 bot 幂等地补建托管工作空间。
资格判定（是否需要托管）与具体开通/幂等/持久化逻辑都在引擎策略
（aicoding 的 hosted-workspace mixin）里；本服务只做 bot 查询、构造上下文、
单次调度，并把策略抛出的"资格未开启"信号转成 BotService 错误。非 aicoding
引擎不提供该 mixin，走默认基类的 not-eligible 默认。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from injector import inject

from agentclaw.community.core.aicoding.protocols import (
    AicodingHostedWorkspaceServiceProtocol,
)
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import (
    WorkspaceHostingService,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotServiceError,
)
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository

log = logging.getLogger("aicoding-hosted-workspace")


class AicodingHostedWorkspaceService(AicodingHostedWorkspaceServiceProtocol):
    """幂等补建托管工作空间（applicationCoding，或显式开启 workspace 能力时）。"""

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        template_service: TemplateService,
        workspace_hosting_service: Optional[WorkspaceHostingService] = None,
    ) -> None:
        self._bot_repo = bot_repo
        self._template_service = template_service
        # 托管工作空间（workspace hosting）是 corp 才安装的依赖；社区列为 None（B8），使用点守卫报错。
        self._workspace_hosting_service = workspace_hosting_service

    def _require_workspace_hosting(self) -> "WorkspaceHostingService":
        """返回 workspace-hosting 服务，未配置时抛清晰错误。"""
        if self._workspace_hosting_service is None:
            raise BotServiceError(
                "Workspace-hosting service is not configured in this deployment; "
                "applicationCoding bots require it."
            )
        return self._workspace_hosting_service

    def ensure_hosted_workspace(self, bot_id: str, user_id: str) -> Optional[str]:
        """为已存在 bot 幂等补建托管工作空间，返回 workspace id。

        1. 已有 ``dima_space_id`` → 由策略直接返回（不重复创建）。
        2. 否则策略调用底层开通并写回 template_config。

        Args:
            bot_id: Bot ID
            user_id: 操作者用户 ID（查询 bot；权限校验在 router 层完成）

        Returns:
            托管工作空间 ID；开通失败时返回 None。

        Raises:
            BotNotFoundError: bot 不存在或当前用户无权访问
            BotServiceError: bot 不是可托管工作空间的 Coding Bot，或本部署未配置托管服务
        """
        # 引擎层与 service 层的入口延迟导入：避免与 engines 注册表在加载期互相触发。
        from agentclaw.community.core.bot_management.engines import (
            HostedWorkspaceNotEligibleError,
            get_engine_provisioning_registry,
            resolve_provisioning,
        )
        from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
            AICODING_ENGINE_TYPE,
        )
        from agentclaw.community.core.bot_management.engines.registry import (
            normalize_engine_type,
        )

        bot = self._bot_repo.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        template_type = bot.get("template_type")
        active_engine = normalize_engine_type(
            bot.get("active_engine") or bot.get("engine_type"),
            default="",
        )
        template_config = self._template_service.get_template_config(bot_id) or {}
        # 上下文仍统一由 resolve_provisioning 构造（owner_id/bot_type/...
        # 单入口），但策略显式取 aicoding：托管工作空间的资格与开通是 aicoding
        # 独有的能力，资格契约（applicationCoding 或显式开启 workspace 能力）
        # 由 aicoding 策略的 _needs 判定。resolve_for_context 对 "applicationCoding
        # + 非编码默认 engine(如仓库插入缺省的 moltis)" 会落到 Default(不托管)，
        # 会让 legacy applicationCoding bot 被误判不可托管；故始终用 aicoding 策略。
        provision_ctx, _engine_strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=str(bot.get("owner_id") or user_id),
            bot_type=str(bot.get("bot_type") or ""),
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        provision_strategy = get_engine_provisioning_registry().resolve(
            AICODING_ENGINE_TYPE
        )
        # 非托管场景由 aicoding 策略 _needs 直接抛 not-eligible，service 翻译为
        # BotServiceError；不做 getattr 探测，单次直调。
        try:
            return provision_strategy.ensure_hosted_workspace(
                provision_ctx,
                bot_name=bot.get("bot_name") or bot_id,
                template_config=template_config,
                template_service=self._template_service,
                workspace_hosting_provider=self._require_workspace_hosting,
            )
        except HostedWorkspaceNotEligibleError as exc:
            raise BotServiceError(
                f"Bot {bot_id} 未开通 workspace 托管能力，无法创建托管工作空间"
                f"（template_type={template_type}, active_engine={active_engine or None}）"
            ) from exc
