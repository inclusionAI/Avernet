"""TaskDiscoveryModule — task_discovery 的 DI 接线。

参考 ``CronModule`` 的模式：
- 绑定 ``TaskDiscoveryLifecycle`` 为 singleton
- Lifecycle 参与者由 ``discover_lifecycle_participants`` 自动发现
- ``TaskDiscoveryLifecycle.__init__`` 通过 @inject 注入 BotService
- 在 backend startup 后自动触发定时调度，为所有用户 bot 执行任务发现

配置项 (通过环境变量):
  TASK_DISCOVERY_AUTO_START        是否启用自动调度 (true/false, 默认 true)
  TASK_DISCOVERY_SCHEDULE_HOUR     调度小时 (默认 11)
  TASK_DISCOVERY_SCHEDULE_MINUTE   调度分钟 (默认 0)
  TASK_DISCOVERY_ENGINE_URL        Engine API 地址 (默认 http://localhost:20003)
  TASK_DISCOVERY_FRONTEND_URL      Engine 前端地址 (默认同 engine URL)
  TASK_DISCOVERY_DATA_FILE         任务数据文件路径
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.core.task.task_discovery.lifecycle import (
    TaskDiscoveryLifecycle,
)


class TaskDiscoveryModule(Module):
    """DI bindings for task discovery."""

    def configure(self, binder: Binder) -> None:
        # Lifecycle 参与者 — startup() 中启动定时调度,
        # shutdown() 中停止。由 discover_lifecycle_participants 自动发现。
        # BotService 由 @inject 自动注入（已由 BotManagementModule 绑定）。
        binder.bind(TaskDiscoveryLifecycle, to=TaskDiscoveryLifecycle, scope=singleton)