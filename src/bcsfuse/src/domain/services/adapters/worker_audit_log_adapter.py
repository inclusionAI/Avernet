"""
WorkerAuditLogAdapter Protocol

Worker 审计日志 Adapter 接口定义。

Stage 1 实现：先做 InMemory，后续替换 SQLite / ELK。

职责：
- 记录 Worker 相关操作
- 变更追溯

Stage 1 最小审计能力：
- action
- worker_id
- actor
- source_type
- before/after 摘要
- timestamp

为什么值得抽 adapter：
- 审计日志可能需要独立的存储系统
- 未来可能接入专业的日志平台（ELK）
- 测试时可以用内存实现
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction


@runtime_checkable
class WorkerAuditLogAdapter(Protocol):
    """
    Audit Log Adapter

    职责：
    - 记录 Worker 相关操作
    - 变更追溯

    Stage 1 实现：InMemory（Phase 1）→ SQLite（Phase 2）
    未来可替换：ELK / 日志系统 / 时序数据库
    """

    def append_log(self, audit_log: WorkerAuditLog) -> None:
        """
        记录审计日志

        Args:
            audit_log: 审计日志
        """
        ...

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
        ...

    def get_latest_log(self, worker_id: str) -> Optional[WorkerAuditLog]:
        """
        获取最新日志

        Args:
            worker_id: Worker ID

        Returns:
            WorkerAuditLog 或 None
        """
        ...


__all__ = ["WorkerAuditLogAdapter"]