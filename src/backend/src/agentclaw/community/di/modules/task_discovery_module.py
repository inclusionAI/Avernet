"""TaskDiscoveryModule — task_discovery 的 DI 接线。

参考 ``CronModule`` 和 ``BotDormantModule`` 的模式：
- 绑定 ``TaskDiscoveryScheduler`` 为 singleton（Lifecycle 参与者自动发现）
- 绑定 ``DiscoveryService`` 为 singleton
- 绑定 ``TaskDiscoveryLockRepository`` 为 singleton（per-bot 分布式锁）
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
from agentclaw.community.api.work_order_service import (
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.repository.implementations.task.discovery_lock import (
    TaskDiscoveryLockRepository,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskDiscoveryLockRepositoryProtocol,
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

#: 默认 db 文件路径(8 级上溯到项目根)
_PROJECT_ROOT = Path(__file__).resolve()
for _ in range(8):
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
        # NOTE: DiscoveryService 不用 binder.bind — _provide_discovery_service
        # 的 @provider @singleton 已处理绑定，binder.bind 会遮盖 provider 导致
        # injector 直接调 __init__() 但无法注入 reader/initiator/notify_sender。
        binder.bind(TaskDiscoveryScheduler, to=TaskDiscoveryScheduler, scope=singleton)
        # Per-bot 分布式锁：单一 ORM 实现，同时运行于 OceanBase (prod) 和
        # SQLite (local)，差异仅在注入的 DatabasePlugin。UNIQUE(env, bot_id,
        # discovery_date) 即锁本体——多机器并发 INSERT 由 DB 原子仲裁。
        binder.bind(
            TaskDiscoveryLockRepositoryProtocol,
            to=TaskDiscoveryLockRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def _provide_discovery_service(
        self,
        reader: TaskReader,
        session_initiator: SessionInitiator,
        notify_sender: NotifySenderPlugin,
        bot_service: _TaskDiscoveryBotServiceProtocol,
        discovery_lock_repo: TaskDiscoveryLockRepositoryProtocol,
        work_order_service: WorkOrderServiceProtocol,
    ) -> DiscoveryService:
        """构建 DiscoveryService（注入 reader + initiator + notify + bot_service + lock + work_order）。"""
        return DiscoveryService(
            reader=reader,
            session_initiator=session_initiator,
            notify_sender=notify_sender,
            bot_service=bot_service,
            discovery_lock_repo=discovery_lock_repo,
            work_order_service=work_order_service,
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