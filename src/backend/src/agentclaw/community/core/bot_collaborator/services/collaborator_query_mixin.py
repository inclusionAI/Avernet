"""协作者查询能力。

将单 Bot 和批量协作者读取从 ``CollaboratorService`` 主模块拆出，保持原有
service API，同时让查询职责独立演进。
"""
from typing import List, Optional

from agentclaw.community.core.bot_collaborator.errors import (
    BotNotFoundError,
    PermissionDeniedError,
)
from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    PermissionLevel,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env


class CollaboratorQueryMixin:
    """为 ``CollaboratorService`` 提供单 Bot 与批量协作者查询。"""

    _bot_repo: BotRepository
    _collaborator_repo: CollaboratorRepositoryProtocol

    def check_permission(
        self,
        bot_pk: int,
        user_id: str,
        owner_id: str,
        required_level: PermissionLevel,
        env: Optional[str] = None,
    ) -> None:
        """由 ``CollaboratorService`` 的权限实现覆盖。"""
        raise NotImplementedError

    def list_collaborators(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]:
        """获取 Bot 的协作者列表。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 请求用户工号
            role: 角色过滤（可选）
            env: 环境标识

        Returns:
            CollaboratorRecord 列表

        Raises:
            BotNotFoundError: Bot 不存在
            PermissionDeniedError: 用户无权限查看
        """
        if env is None:
            env = get_current_env()

        # 1. 查询 Bot 信息
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot 不存在: bot_id={bot_id}, owner_id={owner_id}")

        bot_pk = bot["id"]
        owner_id_from_bot = bot["owner_id"]

        # 2. 检查用户权限（需要 MEMBER 或更高）
        self.check_permission(
            bot_pk=bot_pk,
            user_id=user_id,
            owner_id=owner_id_from_bot,
            required_level=PermissionLevel.MEMBER,
            env=env,
        )

        # 3. 获取协作者列表
        return self._collaborator_repo.list_by_bot(
            bot_id=bot_id,
            owner_id=owner_id_from_bot,
            env=env,
            role=role,
        )

    def batch_list_collaborators(
        self,
        bot_ids: list[str],
        user_id: str,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]:
        """批量查询候选 Bot 的协作者，跳过不存在或当前用户不可访问的 Bot。"""
        if env is None:
            env = get_current_env()

        normalized_bot_ids = list(dict.fromkeys(
            bot_id.strip() for bot_id in bot_ids if bot_id.strip()
        ))
        records: List[CollaboratorRecord] = []
        for bot_id in normalized_bot_ids:
            bot = self._bot_repo.get_by_id(bot_id)
            if not bot:
                continue

            bot_pk = bot["id"]
            owner_id = bot["owner_id"]
            try:
                self.check_permission(
                    bot_pk=bot_pk,
                    user_id=user_id,
                    owner_id=owner_id,
                    required_level=PermissionLevel.MEMBER,
                    env=env,
                )
            except PermissionDeniedError:
                continue

            records.extend(self._collaborator_repo.list_by_bot(
                bot_id=bot_id,
                owner_id=owner_id,
                env=env,
                role=role,
            ))

        return records
