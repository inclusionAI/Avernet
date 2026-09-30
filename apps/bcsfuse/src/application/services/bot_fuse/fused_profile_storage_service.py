"""
FusedProfileStorageService - 融合结果存储服务

统一管理融合结果的内存缓存（L1）和数据库持久化（L2）。

缓存策略：
- G9 模式（bot_profile_fuse）：fusion_id 基于内容哈希，相同输入可复用缓存，命中率高
- G1/G2/G5 模式：fusion_id 每次都是新 UUID，缓存主要服务于进程内会话

根据 fusion-storage-design.md 规范实现。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Optional

from src.domain.enums.fuse_enums import FusionMode, FusionStatus
from src.domain.models.profile_fusion import FusedProfileRecord
from src.domain.models.profile_fusion import ConversationTurn
from src.infra.repositories.fused_profile_repository import FusedProfileRepository
from src.utils.fuse_util import format_participant_ids

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class FusionCache:
    """
    融合结果内存缓存（L1 缓存）

    基于 fusion_id 作为缓存 Key，内容变化时 fusion_id 变化，缓存自动失效。
    TTL 默认 1 天，适用于热数据快速响应。

    注意：
    - G9 模式（bot_profile_fuse）：fusion_id 基于内容哈希，相同内容可复用缓存
    - G1/G2/G5 模式：fusion_id 每次都是新 UUID，缓存主要服务于同一会话内的重复查询
    """

    def __init__(self, ttl_seconds: int = 86400):
        """
        初始化缓存

        Args:
            ttl_seconds: 缓存过期时间（秒），默认 1 天（86400 秒）
        """
        self._cache: dict[str, tuple[FusedProfileRecord, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)

    def get(self, fusion_id: str) -> Optional[FusedProfileRecord]:
        """
        获取缓存

        Args:
            fusion_id: 融合唯一标识

        Returns:
            缓存的记录，不存在或已过期返回 None
        """
        if fusion_id not in self._cache:
            return None

        record, created_at = self._cache[fusion_id]

        # 检查是否过期
        if datetime.now() - created_at > self._ttl:
            del self._cache[fusion_id]
            return None

        return record

    def set(self, fusion_id: str, record: FusedProfileRecord) -> None:
        """
        设置缓存

        Args:
            fusion_id: 融合唯一标识
            record: 融合结果记录
        """
        self._cache[fusion_id] = (record, datetime.now())

    def delete(self, fusion_id: str) -> None:
        """
        删除缓存

        Args:
            fusion_id: 融合唯一标识
        """
        self._cache.pop(fusion_id, None)

    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()

    def cleanup_expired(self) -> int:
        """
        清理过期缓存

        Returns:
            清理的缓存数量
        """
        now = datetime.now()
        expired_keys = [
            k for k, (_, created_at) in self._cache.items()
            if now - created_at > self._ttl
        ]
        for k in expired_keys:
            del self._cache[k]
        return len(expired_keys)


class FusedProfileStorageService:
    """
    融合结果存储服务

    统一管理内存缓存（L1）和数据库持久化（L2）：
    - 查询时自动走缓存策略（先内存，再数据库）
    - 写入时同时更新缓存和数据库
    - 对上层透明，调用方无需关心缓存细节

    使用方式：
        from src.infra.adapters import SQLiteFusedProfileStore  # 或 ZDASFusedProfileStore

        storage_service = FusedProfileStorageService(
            repository=SQLiteFusedProfileStore("data/fusion_session.db"),
            enable_memory_cache=True,
        )

        # 查询
        record = storage_service.find_by_fusion_id(fusion_id)

        # 查询或计算
        record = storage_service.find_or_compute(
            fusion_id,
            compute_fn=lambda: do_fusion(profiles),
        )
    """

    def __init__(
        self,
        repository: FusedProfileRepository,
        enable_memory_cache: bool = True,
        cache_ttl_seconds: int = 86400,
    ):
        """
        初始化存储服务

        Args:
            repository: 数据库 Repository
            enable_memory_cache: 是否启用内存缓存
            cache_ttl_seconds: 缓存过期时间（秒），默认 1 天
        """
        self._repository = repository
        self._memory_cache = FusionCache(ttl_seconds=cache_ttl_seconds) if enable_memory_cache else None

        if enable_memory_cache:
            logger.info("[FusedProfileStorage] 内存缓存已启用，TTL=%d秒", cache_ttl_seconds)
        else:
            logger.info("[FusedProfileStorage] 内存缓存已禁用")

    def find_by_fusion_id(self, fusion_id: str) -> Optional[FusedProfileRecord]:
        """
        查询融合结果（自动走缓存策略）

        查询顺序：内存缓存 → 数据库
        数据库命中时回填内存缓存

        Args:
            fusion_id: 融合唯一标识

        Returns:
            FusedProfileRecord 或 None
        """
        # L1: 检查内存缓存
        if self._memory_cache:
            cached = self._memory_cache.get(fusion_id)
            if cached:
                logger.debug("[FusedProfileStorage] 缓存命中: fusion_id=%s", fusion_id)
                return cached

        # L2: 查询数据库
        record = self._repository.find_by_key(fusion_id)
        if record and self._memory_cache:
            # 回填 L1 缓存
            self._memory_cache.set(fusion_id, record)
            logger.debug("[FusedProfileStorage] 数据库命中，已回填缓存: fusion_id=%s", fusion_id)

        return record

    def find_or_compute(
        self,
        fusion_id: str,
        compute_fn: Callable[[], FusedProfileRecord],
        refresh: bool = False,
    ) -> FusedProfileRecord:
        """
        查询或计算融合结果

        如果缓存中有结果，直接返回；否则执行计算函数并保存结果。

        Args:
            fusion_id: 融合唯一标识
            compute_fn: 计算函数，返回 FusedProfileRecord
            refresh: 是否强制刷新（跳过缓存）

        Returns:
            FusedProfileRecord
        """
        # 非刷新模式，先查缓存
        if not refresh:
            existing = self.find_by_fusion_id(fusion_id)
            if existing and existing.fuse_detail:
                logger.info("[FusedProfileStorage] 复用已有结果: fusion_id=%s", fusion_id)
                return existing

        # 执行计算
        logger.info("[FusedProfileStorage] 执行计算: fusion_id=%s", fusion_id)
        record = compute_fn()

        # 保存结果（同时更新缓存和数据库）
        self.save(record)

        return record

    def save(self, record: FusedProfileRecord) -> str:
        """
        保存融合结果（同时更新内存缓存和数据库）

        Args:
            record: 融合结果记录

        Returns:
            fusion_id
        """
        # 保存到数据库
        fusion_id = self._repository.save(record)
        logger.info("[FusedProfileStorage] 已保存到数据库: fusion_id=%s", fusion_id)

        # 更新内存缓存
        if self._memory_cache:
            self._memory_cache.set(fusion_id, record)
            logger.debug("[FusedProfileStorage] 已更新缓存: fusion_id=%s", fusion_id)

        return fusion_id

    def update(self, record: FusedProfileRecord) -> str:
        """
        更新已存在的融合结果（同时更新内存缓存和数据库）

        Args:
            record: 融合结果记录

        Returns:
            fusion_id
        """
        # 更新数据库
        fusion_id = self._repository.update(record)
        logger.info("[FusedProfileStorage] 已更新数据库: fusion_id=%s", fusion_id)

        # 更新内存缓存
        if self._memory_cache:
            self._memory_cache.set(fusion_id, record)
            logger.debug("[FusedProfileStorage] 已更新缓存: fusion_id=%s", fusion_id)

        return fusion_id

    def save_fused_profile(
        self,
        fusion_id: str,
        fusion_mode: FusionMode,
        participant_ids: list[str],
        fuse_detail: dict,
        profiles: Optional[list[dict]] = None,
        group_id: Optional[str] = None,
        driver_bot_id: Optional[str] = None,
        question: Optional[str] = None,
        created_by: Optional[str] = None,
        refresh: bool = False,
    ) -> str:
        """
        保存融合结果（便捷方法）

        Args:
            fusion_id: 融合ID（由上层统一生成）
            fusion_mode: 融合模式
            participant_ids: 参与者ID列表
            fuse_detail: 融合详情
            profiles: Profile快照列表（用于存储）
            group_id: 关联群组ID
            driver_bot_id: 发起融合的 bot ID
            question: 融合问题
            created_by: 触发融合的用户/系统
            refresh: 是否强制刷新（覆盖已存在的记录）

        Returns:
            fusion_id
        """
        from src.domain.exceptions import DuplicateFusionException

        record = FusedProfileRecord(
            fusion_id=fusion_id,
            fusion_mode=fusion_mode.value,
            group_id=group_id,
            driver_bot_id=driver_bot_id,
            question=question,
            participant_ids=format_participant_ids(participant_ids),
            participant_profile_snapshot=profiles,
            fuse_detail=fuse_detail,
            status=FusionStatus.SUCCESS.value,
            created_by=created_by,
        )

        # refresh 模式：强制更新已存在的记录（清空 conversation）
        if refresh:
            if self.exists(fusion_id):
                logger.info("[FusedProfileStorage] 强制刷新模式，更新已存在的记录: fusion_id=%s", fusion_id)
                return self.update(record)
            else:
                logger.info("[FusedProfileStorage] 强制刷新模式，记录不存在，新建: fusion_id=%s", fusion_id)
                return self.save(record)

        # 非刷新模式：检查是否已存在
        if self.exists(fusion_id):
            logger.info("[FusedProfileStorage] 融合结果已存在，跳过保存: fusion_id=%s", fusion_id)
            return fusion_id

        try:
            return self.save(record)
        except DuplicateFusionException:
            # 并发场景：在检查和保存之间其他请求已创建
            logger.info("[FusedProfileStorage] 融合结果已被其他请求创建: fusion_id=%s", fusion_id)
            return fusion_id

    def append_conversation_turn(
        self,
        fusion_id: str,
        turn: ConversationTurn,
    ) -> None:
        """
        追加对话轮次

        Args:
            fusion_id: 融合唯一标识
            turn: 对话轮次
        """
        self._repository.append_turn(fusion_id, turn)
        logger.debug("[FusedProfileStorage] 已追加对话轮次: fusion_id=%s, turn_index=%d",
                    fusion_id, turn.turn_index)

        # 对话更新后，清理缓存（下次查询时重新加载）
        if self._memory_cache:
            self._memory_cache.delete(fusion_id)

    def update_status(
        self,
        fusion_id: str,
        status: FusionStatus,
        message: Optional[str] = None,
    ) -> None:
        """
        更新执行状态

        Args:
            fusion_id: 融合唯一标识
            status: 执行状态
            message: 执行消息
        """
        self._repository.update_status(
            fusion_id=fusion_id,
            status=status.value,
            fuse_message=message,
        )
        logger.info("[FusedProfileStorage] 已更新状态: fusion_id=%s, status=%s",
                   fusion_id, status.value)

        # 状态更新后，清理缓存
        if self._memory_cache:
            self._memory_cache.delete(fusion_id)

    def invalidate_cache(self, fusion_id: str) -> None:
        """
        手动使缓存失效

        Args:
            fusion_id: 融合唯一标识
        """
        if self._memory_cache:
            self._memory_cache.delete(fusion_id)
            logger.debug("[FusedProfileStorage] 已清理缓存: fusion_id=%s", fusion_id)

    def find_by_participant(
        self,
        participant_id: str,
        limit: int = 20,
        fusion_mode: Optional[FusionMode] = None,
    ) -> list[FusedProfileRecord]:
        """
        查询某个专家参与的融合

        Args:
            participant_id: 参与者 ID
            limit: 返回数量限制
            fusion_mode: 融合模式过滤

        Returns:
            FusedProfileRecord 列表
        """
        mode_value = fusion_mode.value if fusion_mode else None
        return self._repository.find_by_participant(
            participant_id=participant_id,
            limit=limit,
            fusion_mode=mode_value,
        )

    def find_by_group(
        self,
        group_id: str,
        limit: int = 20,
        fusion_mode: Optional[FusionMode] = None,
    ) -> list[FusedProfileRecord]:
        """
        查询某个群组的融合记录

        Args:
            group_id: 群组 ID
            limit: 返回数量限制
            fusion_mode: 融合模式过滤

        Returns:
            FusedProfileRecord 列表
        """
        mode_value = fusion_mode.value if fusion_mode else None
        return self._repository.find_by_group(
            group_id=group_id,
            limit=limit,
            fusion_mode=mode_value,
        )

    def exists(self, fusion_id: str) -> bool:
        """
        检查记录是否存在

        Args:
            fusion_id: 融合唯一标识

        Returns:
            是否存在
        """
        return self._repository.exists(fusion_id)

    def get_conversation(
        self,
        fusion_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> Optional[dict]:
        """
        获取对话

        Args:
            fusion_id: 融合唯一标识
            offset: 偏移量
            limit: 返回数量限制

        Returns:
            包含对话列表和统计信息的字典
        """
        return self._repository.get_conversation(
            fusion_id=fusion_id,
            offset=offset,
            limit=limit,
        )


__all__ = [
    "FusedProfileStorageService",
]