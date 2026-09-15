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

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.bot_service import (
    BotServiceProtocol as _ApiBotServiceProtocol,
)
from agentclaw.community.api.cron_relay_service import (
    CronRelayServiceProtocol as _ApiCronRelayServiceProtocol,
)
from agentclaw.community.api.work_order_service import (
    WorkOrderServiceProtocol as _ApiWorkOrderServiceProtocol,
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
from agentclaw.community.core.task.task_discovery.frontend_url import (
    ConfigFrontendUrlProvider,
)
from agentclaw.community.core.task.task_discovery.notify_messages_provider import (
    NotifyMessagesProvider,
)
from agentclaw.community.core.task.task_discovery.protocols import (
    BotServiceProtocol as _TaskDiscoveryBotServiceProtocol,
    CronRelayServiceProtocol as _TaskDiscoveryCronRelayProtocol,
    WorkOrderServiceProtocol as _TaskDiscoveryWorkOrderServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.scheduler import (
    TaskDiscoveryScheduler,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    CronRelaySessionInitiator,
    SessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    OrmTaskReader,
    TaskReader,
)
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


_DEFAULT_BACKEND_URL = "http://localhost:8888"
_DEFAULT_FRONTEND_URL = "http://localhost:8000"


def _resolve_frontend_url() -> str:
    """Resolve frontend workbench URL — env-aware fallback chain.

    Priority: ``FRONTEND_URL`` env > ``SINGLEBOX_FRONTEND_URL`` env (singlebox)
    > ``http://localhost:8000``.

    Does NOT inline corporate DNS names (satisfies the OSS architecture gate
    ``test_shipped_config_no_corp_identifiers``). The singlebox env overlay sets
    ``SINGLEBOX_FRONTEND_URL`` to the local domain; the community source defaults
    to ``localhost``.
    """
    url = os.environ.get("FRONTEND_URL")
    if url:
        return url
    if os.environ.get("DEPLOY_PROFILE", "").strip().lower() == DeployProfile.SINGLEBOX.value:
        return os.environ.get("SINGLEBOX_FRONTEND_URL", _DEFAULT_FRONTEND_URL)
    return _DEFAULT_FRONTEND_URL


def _resolve_backend_url() -> str:
    """Resolve backend self URL — env-aware fallback chain.

    Priority: ``BACKEND_URL`` env > ``SINGLEBOX_BACKEND_URL`` env (singlebox)
    > ``http://localhost:8888``.

    Mirrors ``task_module.py._resolve_api_base_url``: env-aware, no inline corp DNS.
    """
    url = os.environ.get("BACKEND_URL")
    if url:
        return url
    if os.environ.get("DEPLOY_PROFILE", "").strip().lower() == DeployProfile.SINGLEBOX.value:
        return os.environ.get("SINGLEBOX_BACKEND_URL", _DEFAULT_BACKEND_URL)
    return _DEFAULT_BACKEND_URL


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
        notify_sender: NotifyMessagesProvider,
        bot_service: _TaskDiscoveryBotServiceProtocol,
        discovery_lock_repo: TaskDiscoveryLockRepositoryProtocol,
        work_order_service: _TaskDiscoveryWorkOrderServiceProtocol,
        injector: Injector,
    ) -> DiscoveryService:
        """构建 DiscoveryService（注入 reader + initiator + notify + bot_service + lock + work_order + frontend_url_provider）。"""
        logger.debug("[task_discovery] → TaskDiscoveryModule._provide_discovery_service()")
        try:
            fe_provider: ConfigFrontendUrlProvider = injector.get(
                ConfigFrontendUrlProvider
            )
        except Exception:  # noqa: BLE101 未绑定 → 默认空值(构造参数兜底)
            fe_provider = ConfigFrontendUrlProvider()
        return DiscoveryService(
            reader=reader,
            session_initiator=session_initiator,
            notify_sender=notify_sender,
            bot_service=bot_service,
            discovery_lock_repo=discovery_lock_repo,
            work_order_service=work_order_service,
            frontend_url_provider=fe_provider,
        )

    @singleton
    @provider
    @inject
    def _provide_session_initiator(
        self,
        cron_relay: _ApiCronRelayServiceProtocol,
        injector: Injector,
    ) -> SessionInitiator:
        """构建 SessionInitiator — 默认 local 实现 (CronRelaySessionInitiator).

        组合根按 ``DeployProfile`` 选实现, provider 内不 if/else:
        - base (本 provider) → ``CronRelaySessionInitiator`` (relay + WebSocket 直连)。
        - corp 列 → 通过 corp overlay 的 ``@provider`` 绑定 ``OpenApiBotSessionInitiator``
          (BaaS Open API + Bearer), last-binding-wins 覆盖本默认绑定。
        - 若 corp 未装/OpenApiBotPort 缺失, corp overlay 自身 fail-closed 回落
          (见 corp ``corp_task_integration``), 不在此 base 内判断。

        ``ConfigFrontendUrlProvider`` 由 DI 注入 (corp 列经钉钉块 env-aware 固化,
        community 列经 user_config.task_discovery 中性块, 未配置→空值)。「取 URL」
        是数据差异而非行为差异, 故不再走 plugin 契约/分列实现。
        """
        logger.debug("[task_discovery] → TaskDiscoveryModule._provide_session_initiator()")
        try:
            fe_provider: ConfigFrontendUrlProvider = injector.get(
                ConfigFrontendUrlProvider
            )
        except Exception:  # noqa: BLE101 未绑定 → 默认空值(构造参数兜底)
            fe_provider = ConfigFrontendUrlProvider()

        return CronRelaySessionInitiator(
            cron_relay=cron_relay,
            frontend_url=_resolve_frontend_url(),
            backend_url=_resolve_backend_url(),
            frontend_url_provider=fe_provider,
        )

    @singleton
    @provider
    @inject
    def _provide_task_reader(self, db: DatabasePlugin) -> TaskReader:
        """构建 OrmTaskReader（注入 DatabasePlugin）。

        corp 走 ZDAS/OceanBase，local 走 SQLite 内存库。
        替代原 SqliteTaskReader 的直接 sqlite3 文件访问。
        """
        return OrmTaskReader(db)

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

    @singleton
    @provider
    @inject
    def _bridge_work_order_protocol(
        self,
        work_order_service: _ApiWorkOrderServiceProtocol,
    ) -> _TaskDiscoveryWorkOrderServiceProtocol:
        """Adapt the API work-order service to the task_discovery local contract.

        WorkOrderService structurally satisfies the local Protocol (has
        create_work_order_event), so no adapter wrapper is needed — just
        return the instance directly.
        """
        return work_order_service  # type: ignore[return-value]