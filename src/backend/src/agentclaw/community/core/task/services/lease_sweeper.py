"""兜底租期清扫器(§10.3 / FR-EXT-04):周期性扫过期 RUNNING 节点收回→接力,防 bot 崩溃卡死。

机械薄封装:只调 ``TaskService.sweep_expired_leases()``。周期触发(常驻定时器/
调度)属部署接入,不在本类——DI 绑定后调度器即可注入 ``LeaseSweeper`` 并按需调
``sweep_once()``(spec §7.2 评审项)。状态在事件日志 + 图谱,本类无状态。

Avernet 规则:``from __future__ import annotations``;``Optional[T]`` 非 ``T | None``;
``@inject`` 构造注入。
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.protocols import TaskService


class LeaseSweeper:
    """BBS 兜底租期清扫器(无状态;状态在事件日志 + 图谱)。"""

    @inject
    def __init__(self, task_service: TaskService) -> None:
        self._svc = task_service

    def sweep_once(self) -> int:
        """扫一次过期租约,返回收回节点数(过期 RUNNING → FAILED/lease_expired)。"""
        return self._svc.sweep_expired_leases()


__all__ = ["LeaseSweeper"]
