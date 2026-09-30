"""
Worker Profile Ingestion Service

Worker Profile Ingestion Baseline

摄取服务，协调 WorkerProfileSource 完成数据摄取。
"""

from __future__ import annotations

from typing import Optional

from src.domain.models.worker_profile import WorkerProfileScanResult
from src.domain.services.worker_profile_source import WorkerProfileSource


class WorkerProfileIngestionService:
    """
    Worker Profile 摄取服务

    职责：
    - 协调一个或多个 WorkerProfileSource
    - 提供统一的摄取入口
    - 管理缓存

    不负责：
    - 复杂的数据转换
    - 向量化
    - 持久化
    """

    def __init__(self, source: WorkerProfileSource):
        """
        初始化服务

        Args:
            source: Worker Profile 来源
        """
        self._source = source
        self._cached_result: Optional[WorkerProfileScanResult] = None

    def ingest(self, refresh: bool = False) -> WorkerProfileScanResult:
        """
        执行摄取

        Args:
            refresh: 是否强制刷新缓存

        Returns:
            WorkerProfileScanResult: 摄取结果
        """
        if refresh or self._cached_result is None:
            self._cached_result = self._source.scan()

        return self._cached_result

    def clear_cache(self) -> None:
        """清除缓存"""
        self._cached_result = None
        # 同时清除 source 的缓存
        if hasattr(self._source, "clear_cache"):
            self._source.clear_cache()


__all__ = ["WorkerProfileIngestionService"]