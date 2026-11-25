"""协作变更的既有副作用段（原样提取）。

``CollaboratorService.on_collaboration_changed`` 的 Passport / 运行设备
``.credentials`` 同步段从服务文件移到这里（拆行数、不动语义）：

- 结果列表（原 TODO 占位的通知结果）；
- admin 协作者收敛后同步 Passport（``admins=[]`` 语义是清空，绝不能短
  路成 ``None``；失败只 warning，不回滚已完成的主流程）；
- admin 工号写入运行设备 ``.credentials`` 的 ``ADMINS=`` 行，委托
  ``DeviceCredentialsAdminsWriter``：对 service bot 解析在线 binding、
  对其它 bot 回退 ``resolve_for_bot``；失败只 warning。

本提取不改动设备写入机制本身，也不扩大任何失败处理；BCS 管理集同步
的旁路通知不属于这里，仍在 ``on_collaboration_changed`` 内、本副作用
段之前提交。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Dict, List

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRole,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
    from agentclaw.community.core.bot_collaborator.services.credentials_admins_writer import (
        DeviceCredentialsAdminsWriter,
    )
    from agentclaw.community.plugins.passport import PassportPlugin

logger = get_logger()

__all__ = ["CollaborationEffects"]


class CollaborationEffects:
    """协作关系变更后的既有副作用收敛点。"""

    def __init__(
        self,
        *,
        passport_plugin: "PassportPlugin",
        credentials_admins_writer: "DeviceCredentialsAdminsWriter",
    ) -> None:
        self._passport_plugin = passport_plugin
        self._creds_writer = credentials_admins_writer

    def apply(
        self,
        bot_id: str,
        owner_id: str,
        collaborators: Sequence["CollaboratorRecord"],
    ) -> List[Dict[str, Any]]:
        """执行原回调的结果/Passport/credentials 段并返回结果列表。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            collaborators: 变更后的完整协作者名单

        Returns:
            处理结果列表，每个元素包含 user_id / notified
        """
        # 1. 通知所有协作者（TODO: 实现实际通知逻辑）
        results: List[Dict[str, Any]] = []

        logger.info(
            "[on_collaboration_changed] Results: %s, bot_id=%s, owner_id=%s",
            results,
            bot_id,
            owner_id,
        )

        # 2. 同步 admin 协作者到 Passport 服务
        # 注意：Passport 服务协议中 admins=[] 表示清空、admins=None 表示不更新；
        # 这里要把 admin 全部移除时必须传 []，不能短路成 None。
        admins = [c.user_id for c in collaborators if c.role == CollaboratorRole.ADMIN]
        try:
            self._passport_plugin.update_passport(
                bot_id=bot_id,
                user_id=owner_id,
                admins=admins,
            )
            logger.info(
                "[on_collaboration_changed] Synced admins to the passport service: bot_id=%s, admins=%s",
                bot_id,
                admins,
            )
        except Exception as e:
            logger.warning(
                "[on_collaboration_changed] Failed to sync admins to the passport service: bot_id=%s, error=%s",
                bot_id,
                e,
            )

        # 3. 同步 admin 工号到运行设备的 .credentials 文件（ADMINS= 行）。
        # 委托给 DeviceCredentialsAdminsWriter:对 service bot 解析在线 binding
        # (ext.binding.online)、对其它 bot 回退 resolve_for_bot;失败只 warning，
        # 不影响协作者变更主流程，也不回滚已完成的 Passport 服务同步。
        self._creds_writer.sync_on_change(bot_id, owner_id, admins)

        return results
