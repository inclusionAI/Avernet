"""TaskDiscoveryModule — task_discovery 的 DI 接线。

参考 ``CronModule`` 和 ``BotDormantModule`` 的模式：
- 绑定 ``TaskDiscoveryScheduler`` 为 singleton（Lifecycle 参与者自动发现）
- 绑定 ``DiscoveryService`` 为 singleton
- 提供 ``SessionInitiator``（注入 CronRelayServiceProtocol）
- 提供 ``TaskReader``（注入 SQLite path）
- 桥接 API 层的 BotServiceProtocol 和 CronRelayServiceProtocol

配置项 (通过环境变量):
  TASK_DISCOVERY_AUTO_START        是否启用自动调度 (true/false, 默认 true)
  TASK_DISCOVERY_CRON              cron 表达式 (默认 "0 11 * * *")
  TASK_DISCOVERY_TIMEZONE          调度时区 (默认 "Asia/Shanghai")
  TASK_DISCOVERY_DATA_FILE         任务数据文件路径
"""
from __future__ import annotations

import os
from pathlib import Path

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_service import (
    BotServiceProtocol as _ApiBotServiceProtocol,
)
from agentclaw.community.api.cron_relay_service import (
    CronRelayServiceProtocol as _ApiCronRelayServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.protocols import (
    BotServiceProtocol as _TaskDiscoveryBotServiceProtocol,
    CronRelayServiceProtocol as _TaskDiscoveryCronRelayProtocol,
)
from agentclaw.community.core.task.task_discovery.scheduler import (
    TaskDiscoveryScheduler,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    CronRelaySessionInitiator,
    SessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
    TaskReader,
)
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin

#: 默认 db 文件路径(9 级上溯到项目根)
_PROJECT_ROOT = Path(__file__).resolve()
for _ in range(9):
    _PROJECT_ROOT = _PROJECT_ROOT.parent
_DEFAULT_DB = str(
    _PROJECT_ROOT / "scripts" / ".dependencies" / "data" / "discovered_tasks.db"
)


def _resolve_db_path() -> str:
    """从环境变量或默认路径解析 db 文件路径。"""
    return os.environ.get("TASK_DISCOVERY_DATA_FILE", _DEFAULT_DB)


class TaskDiscoveryModule(Module):
    """DI bindings for task discovery."""

    def configure(self, binder: Binder) -> None:
        # Lifecycle 参与者 — startup() 中启动 cron 调度,
        # shutdown() 中停止。由 discover_lifecycle_participants 自动发现。
        binder.bind(TaskDiscoveryScheduler, to=TaskDiscoveryScheduler, scope=singleton)
        binder.bind(DiscoveryService, to=DiscoveryService, scope=singleton)

    @singleton
    @provider
    @inject
    def _provide_discovery_service(
        self,
        reader: TaskReader,
        session_initiator: SessionInitiator,
        notify_sender: NotifySenderPlugin,
        bot_service: _TaskDiscoveryBotServiceProtocol,
    ) -> DiscoveryService:
        """构建 DiscoveryService（注入 reader + initiator + notify + bot_service）。"""
        return DiscoveryService(
            reader=reader,
            session_initiator=session_initiator,
            notify_sender=notify_sender,
            bot_service=bot_service,
        )

    @singleton
    @provider
    @inject
    def _provide_session_initiator(
        self,
        cron_relay: _ApiCronRelayServiceProtocol,
    ) -> SessionInitiator:
        """构建 CronRelaySessionInitiator（注入 cron relay）。"""
        return CronRelaySessionInitiator(cron_relay=cron_relay)

    @singleton
    @provider
    def _provide_task_reader(self) -> TaskReader:
        """构建 SqliteTaskReader（注入 db path）。"""
        return SqliteTaskReader(_resolve_db_path())

    @singleton
    @provider
    @inject
    def _bridge_bot_service_protocol(
        self,
        bot_service: _ApiBotServiceProtocol,
    ) -> _TaskDiscoveryBotServiceProtocol:
        """Adapt the API service to the task_discovery module's local contract.

        BotService structurally satisfies the local Protocol (has list_bots/get_bot),
        so no adapter wrapper is needed — just return the instance directly.
        """
        return bot_service  # type: ignore[return-value]

    @singleton
    @provider
    @inject
    def _bridge_cron_relay_protocol(
        self,
        cron_relay: _ApiCronRelayServiceProtocol,
    ) -> _TaskDiscoveryCronRelayProtocol:
        """Adapt the API cron relay to the task_discovery module's local contract."""
        return cron_relay  # type: ignore[return-value]