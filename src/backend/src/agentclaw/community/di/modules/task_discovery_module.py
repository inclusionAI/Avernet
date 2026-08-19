"""TaskDiscoveryModule — task_discovery 的 DI 接线。

参考 ``CronModule`` 和 ``BotDormantModule`` 的模式：
- 绑定 ``TaskDiscoveryLifecycle`` 为 singleton
- Lifecycle 参与者由 ``discover_lifecycle_participants`` 自动发现
- ``TaskDiscoveryLifecycle.__init__`` 通过 @inject 注入
  ``BotServiceProtocol``（core/task/task_discovery 本地定义）和
  ``NotifySenderPlugin``
- ``BotServiceProtocol`` 由本模块的 ``_bridge_bot_service_protocol``
  provider 从 API 层的 ``BotServiceProtocol``（由 BotManagementModule
  绑定到实际 BotService）桥接而来；BotService 结构性满足本地 Protocol
- 在 backend startup 后自动触发定时调度，为所有用户 bot 执行任务发现

配置项 (通过环境变量):
  TASK_DISCOVERY_AUTO_START        是否启用自动调度 (true/false, 默认 true)
  TASK_DISCOVERY_SCHEDULE_HOUR     调度小时 (默认 11)
  TASK_DISCOVERY_SCHEDULE_MINUTE   调度分钟 (默认 0)
  TASK_DISCOVERY_DATA_FILE         任务数据文件路径
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_service import (
    BotServiceProtocol as _ApiBotServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.lifecycle import (
    TaskDiscoveryLifecycle,
)
from agentclaw.community.core.task.task_discovery.protocols import (
    BotServiceProtocol as _TaskDiscoveryBotServiceProtocol,
)


class TaskDiscoveryModule(Module):
    """DI bindings for task discovery."""

    def configure(self, binder: Binder) -> None:
        # Lifecycle 参与者 — startup() 中启动定时调度,
        # shutdown() 中停止。由 discover_lifecycle_participants 自动发现。
        binder.bind(TaskDiscoveryLifecycle, to=TaskDiscoveryLifecycle, scope=singleton)

    @singleton
    @provider
    @inject
    def _bridge_bot_service_protocol(
        self,
        bot_service: _ApiBotServiceProtocol,
    ) -> _TaskDiscoveryBotServiceProtocol:
        """Adapt the API service to the task_discovery module's local contract.

        BotService structurally satisfies the local Protocol (has list_bots),
        so no adapter wrapper is needed — just return the instance directly.
        """
        return bot_service  # type: ignore[return-value]
