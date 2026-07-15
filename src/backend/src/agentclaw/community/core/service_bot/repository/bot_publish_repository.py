"""BotPublish Repository Protocol - 业务层内部抽象。

定义 BotPublish 数据访问的接口，供 Service 层依赖注入使用。
"""
from typing import Dict, Any, List, Optional, Protocol

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord


class BotPublishRepositoryProtocol(Protocol):
    """Bot发布记录数据访问接口。

    所有方法使用字典或 Pydantic 模型作为输入输出，
    具体实现由 plugins 提供。

    Implementation: a single unified ORM body
    (plugins.bot_publish_repository.BotPublishRepository) runs on
    both prod OceanBase and local SQLite via the injected
    DatabasePlugin.
    """

    # ========================================================================
    # Insert Operations
    # ========================================================================

    def insert(self, data: Dict[str, Any]) -> BotPublishRecord:
        """
        创建发布记录。

        Args:
            data: 包含发布记录字段的字典
                - source_bot_pk: ac_bots 表主键
                - source_bot_id: 源 bot_id
                - publish_bot_id: 发布后的 bot_id
                - name: bot名称
                - owner_id: 工号
                - permission_owner: 权限归属
                - ... 其他可选字段

        Returns:
            创建的 BotPublishRecord
        """
        ...

    # ========================================================================
    # Query Operations
    # ========================================================================

    def get_by_id(self, publish_id: int) -> Optional[BotPublishRecord]:
        """
        根据ID获取发布记录。

        Args:
            publish_id: 记录ID

        Returns:
            BotPublishRecord，不存在返回None
        """
        ...

    def get_by_publish_bot_id(
        self,
        publish_bot_id: str,
        owner_id: str,
        env: str,
        publish_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """
        根据 publish_bot_id 和 owner_id 获取最新版本记录。

        Args:
            publish_bot_id: 发布后的 bot_id
            owner_id: 工号
            env: 环境
            publish_status: 发布状态过滤（可选）

        Returns:
            BotPublishRecord，不存在返回None
        """
        ...

    def get_draft_by_publish_bot_id(
        self,
        publish_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """根据 publish_bot_id 获取 DRAFT 行（不按 owner_id 过滤）。

        publish_bot_id 唯一标识一个 bot，故无需 owner_id —— 这避免了调用方
        owner_id 与建单 owner_id 不一致（如 org bot 的 entity_id != 工号）时
        静默查不到的问题。仅用于 record_draft_artifact。

        Args:
            publish_bot_id: 发布后的 bot_id（草稿期 == 源 bot_id）
            env: 环境

        Returns:
            DRAFT 状态的 BotPublishRecord，不存在返回 None
        """
        ...

    def get_by_publish_bot_id_and_version(
        self,
        publish_bot_id: str,
        owner_id: str,
        version: int,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """
        根据 publish_bot_id、owner_id 和 version 获取记录。

        Args:
            publish_bot_id: 发布后的 bot_id
            owner_id: 工号
            version: 版本号
            env: 环境

        Returns:
            BotPublishRecord，不存在返回None
        """
        ...

    def list_by_owner(
        self,
        owner_id: str,
        env: str,
        status: Optional[str] = None,
    ) -> List[BotPublishRecord]:
        """
        查询用户的发布记录列表。

        Args:
            owner_id: 工号
            env: 环境
            status: 状态过滤（可选）

        Returns:
            BotPublishRecord 列表
        """
        ...

    def list_by_source_bot(
        self,
        source_bot_pk: int,
        env: str,
    ) -> List[BotPublishRecord]:
        """
        查询源 bot 的发布记录列表。

        Args:
            source_bot_pk: ac_bots 表主键
            env: 环境

        Returns:
            BotPublishRecord 列表
        """
        ...

    def list_by_status(
        self,
        status: str,
        env: str,
    ) -> List[BotPublishRecord]:
        """
        根据状态查询发布记录列表。

        Args:
            status: 发布状态
            env: 环境

        Returns:
            BotPublishRecord 列表
        """
        ...

    def get_latest_by_source_bot_id_and_owner_and_status(
        self,
        source_bot_id: str,
        owner_id: str,
        status: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """按 source_bot_id + owner_id + status 查询最新发布记录。"""
        ...

    def get_latest_success_by_source_bot_id(
        self,
        source_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        """按 source_bot_id 查询最新一条 status=success 的发布记录（owner 无关）。

        供多实例入口 bot_id → 运行态 binding_id 解析用：取该记录
        ``ext.binding.online``。刻意不按 owner_id 过滤 —— org bot 的
        entity_id 可能与建单 owner_id（工号）不一致，owner 过滤会静默查不到。

        Args:
            source_bot_id: 源 bot_id
            env: 环境

        Returns:
            最新 success 发布记录，不存在返回 None
        """
        ...

    def get_by_last_pub_id(
        self,
        last_pub_id: int,
    ) -> Optional[BotPublishRecord]:
        """
        根据上次发布成功 ID 查询发布记录（幂等性支持）。

        Args:
            last_pub_id: 上次发布成功的记录 ID

        Returns:
            BotPublishRecord，不存在返回 None

        Note:
            用于升级操作的幂等查询，确保重试时不会重复创建新发布单。
        """
        ...

    # ========================================================================
    # Update Operations
    # ========================================================================

    def update_status(
        self,
        publish_id: int,
        target_status: str,
        source_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """
        更新发布状态。

        Args:
            publish_id: 记录ID
            target_status: 目标状态
            source_status: 源状态（可选），传入时只有当前状态等于源状态才能更新成功

        Returns:
            更新后的 BotPublishRecord，源状态不匹配时返回 None
        """
        ...

    def update_status_with_ext(
        self,
        publish_id: int,
        target_status: str,
        ext: Dict[str, Any],
        source_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """
        更新发布状态和扩展字段（乐观锁）。

        Args:
            publish_id: 记录ID
            target_status: 目标状态
            ext: 扩展字段字典
            source_status: 源状态（可选），传入时只有当前状态等于源状态才能更新成功

        Returns:
            更新后的 BotPublishRecord，源状态不匹配时返回 None
        """
        ...

    def rollback_flip(
        self,
        *,
        current_id: int,
        current_ext: Dict[str, Any],
        current_source_status: str,
        current_target_status: str,
        target_id: int,
        target_ext: Dict[str, Any],
        target_source_status: str,
        target_target_status: str,
    ) -> tuple[bool, bool]:
        """原子地翻转回滚的两条发布单状态（同一事务）。

        current: source→target（如 SUCCESS→DRAFT），写入 current_ext。
        target:  source→target（如 UPGRADED→SUCCESS），写入 target_ext。
        两条 UPDATE 在同一事务内提交，避免“翻转一半”导致 can_rollback 永久拒绝
        的半回滚死局（#197）。均为乐观锁 CAS。

        Returns:
            (current_ok, target_ok): 各自 CAS 是否命中。
        """
        ...

    def update_version(
        self,
        publish_id: int,
        version: int,
        status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        """
        更新版本号（可选更新状态）。

        Args:
            publish_id: 记录ID
            version: 新版本号
            status: 新状态（可选）

        Returns:
            更新后的 BotPublishRecord
        """
        ...

    def update_last_pub_id(
        self,
        publish_id: int,
        last_pub_id: int,
    ) -> Optional[BotPublishRecord]:
        """
        更新上次发布成功ID。

        Args:
            publish_id: 记录ID
            last_pub_id: 上次发布成功ID

        Returns:
            更新后的 BotPublishRecord
        """
        ...

    # ========================================================================
    # Delete Operations
    # ========================================================================

    def delete(self, publish_id: int) -> bool:
        """
        删除发布记录。

        Args:
            publish_id: 记录ID

        Returns:
            是否成功
        """
        ...
