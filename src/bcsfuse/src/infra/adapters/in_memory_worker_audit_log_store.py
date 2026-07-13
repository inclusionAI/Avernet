"""
InMemory Worker Audit Log Store

Worker 审计日志的内存存储实现。

Stage 1 Phase 1：用于测试和快速验证。
"""

from __future__ import annotations

from typing import Optional

from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction


class InMemoryWorkerAuditLogStore:
    """
    Worker Audit Log 内存存储

    Stage 1 Phase 1 实现：
    - 纯内存存储（list）
    - 无持久化
    - 非线程安全

    用于：
    - 测试
    - 快速验证业务逻辑
    - 契约测试
    """

    def __init__(self):
        """初始化空仓库"""
        self._logs: list[WorkerAuditLog] = []

    def append_log(self, audit_log: WorkerAuditLog) -> None:
        """
        记录审计日志

        Args:
            audit_log: 审计日志
        """
        self._logs.append(audit_log.model_copy(deep=True))

    def list_logs(
        self,
        worker_id: Optional[str] = None,
        actions: Optional[list[WorkerAuditAction]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkerAuditLog]:
        """
        查询审计日志

        Args:
            worker_id: 过滤 Worker ID（可选）
            actions: 过滤动作类型（可选）
            limit: 分页限制
            offset: 分页偏移

        Returns:
            WorkerAuditLog 列表
        """
        result = self._logs

        # 按 worker_id 过滤
        if worker_id is not None:
            result = [log for log in result if log.worker_id == worker_id]

        # 按 action 过滤
        if actions is not None and len(actions) > 0:
            result = [log for log in result if log.action in actions]

        # 按时间降序排序
        result = sorted(result, key=lambda log: log.performed_at, reverse=True)

        # 分页
        if offset > 0:
            result = result[offset:]
        if limit > 0:
            result = result[:limit]

        return [log.model_copy(deep=True) for log in result]

    def get_latest_log(self, worker_id: str) -> Optional[WorkerAuditLog]:
        """
        获取最新日志

        Args:
            worker_id: Worker ID

        Returns:
            WorkerAuditLog 或 None
        """
        logs = self.list_logs(worker_id=worker_id, limit=1)
        if logs:
            return logs[0]
        return None

    def clear(self) -> None:
        """清空仓库（用于测试清理）"""
        self._logs.clear()


__all__ = ["InMemoryWorkerAuditLogStore"]