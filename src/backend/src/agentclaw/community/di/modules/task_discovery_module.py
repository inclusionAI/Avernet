"""TaskDiscoveryModule — task_discovery 的 DI 接线。

参考 ``CronModule`` 和 ``BotDormantModule`` 的模式：
- 绑定 ``TaskDiscoveryScheduler`` 为 singleton（Lifecycle 参与者自动发现）
- 绑定 ``DiscoveryService`` 为 singleton
- 绑定 ``TaskDiscoveryLockRepository`` 为 singleton（per-bot 分布式锁）
- 提供 ``SessionInitiator``（唯一实现 ``OpenApiBotSessionInitiator``，注入
  ``OpenApiBotPort``；port 未绑定 → ``UnavailableSessionInitiator`` fail-closed
  占位。2026-09-15 统一化: 原 ``CronRelaySessionInitiator`` Relay/WS 链已废除，
  corp 列不再覆盖绑定）
- 提供 ``TaskReader``（注入 SQLite path）
- 桥接 API 层的 BotServiceProtocol 和 WorkOrderServiceProtocol

配置项 (通过环境变量):
  TASK_DISCOVERY_AUTO_START        是否启用自动调度 (true/false, 默认 true)
  TASK_DISCOVERY_CRON              cron 表达式 (默认 "0 11 * * *")
  TASK_DISCOVERY_TIMEZONE          调度时区 (默认 "Asia/Shanghai")
  TASK_DISCOVERY_DATA_FILE         任务数据文件路径
"""

from __future__ import annotations

from urllib.parse import urlparse

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.bot_service import (
    BotServiceProtocol as _ApiBotServiceProtocol,
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
    WorkOrderServiceProtocol as _TaskDiscoveryWorkOrderServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.scheduler import (
    TaskDiscoveryScheduler,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    OpenApiBotSessionInitiator,
    SessionInitiator,
    UnavailableSessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    OrmTaskReader,
    TaskReader,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    BcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    OpenApiBotPort,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


_DEFAULT_BACKEND_URL = "http://localhost:8888"
_DEFAULT_FRONTEND_URL = "http://localhost:8000"


def _backend_origin_from_callback_url(provider: BcsTokenProvider | None) -> str:
    """Return the backend origin from the standard task callback configuration.

    ``bcs_client.task_callback_url[_pre]`` already points BCS at this backend.
    Reuse that deployment-neutral value instead of introducing a parallel URL
    axis; absent configuration retains the local default for lightweight tests.
    """
    callback_url = provider.task_callback_url if provider is not None else ""
    if not callback_url:
        return _DEFAULT_BACKEND_URL
    parsed = urlparse(callback_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _DEFAULT_BACKEND_URL
    return f"{parsed.scheme}://{parsed.netloc}"


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
        logger.debug(
            "[task_discovery] → TaskDiscoveryModule._provide_discovery_service()"
        )
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
        injector: Injector,
    ) -> SessionInitiator:
        """构建 SessionInitiator — 唯一实现 ``OpenApiBotSessionInitiator`` (BaaS Open API)。

        2026-09-15 统一化: 原 base 绑定 ``CronRelaySessionInitiator``（relay +
        WebSocket 直连 engine 链）已废除;实现自 corp 列下沉为社区唯一基绑定,
        依赖组合根经 DI 提供的 ``OpenApiBotPort``:
        - corp/pre/prod 列 → ``CorpTaskIntegrationModule.openapi_bot_port``
          (openapi_bot 块 api_key_secret → Mist, Bearer)。
        - 社区/单机列 → 无凭证,``OpenApiBotPort`` 未绑定时注入
          ``UnavailableSessionInitiator`` fail-closed 占位（调用即抛可读错误,
          由 DiscoveryService per-bot 容错记录）;e2e/联调可显式注入本地 port stub。

        ``ConfigFrontendUrlProvider`` 由 DI 注入 (corp 列经钉钉块 env-aware 固化,
        community 列经 user_config.task_discovery 中性块, 未配置→空值)。「取 URL」
        是数据差异而非行为差异, 故不再走 plugin 契约/分列实现。
        """
        logger.debug(
            "[task_discovery] → TaskDiscoveryModule._provide_session_initiator()"
        )
        try:
            fe_provider: ConfigFrontendUrlProvider = injector.get(
                ConfigFrontendUrlProvider
            )
        except Exception:  # noqa: BLE101 未绑定 → 默认空值(构造参数兜底)
            fe_provider = ConfigFrontendUrlProvider()

        try:
            bcs_identity_provider: BcsTokenProvider | None = injector.get(BcsTokenProvider)
        except Exception:  # noqa: BLE101 未绑定 → 本地默认 origin
            bcs_identity_provider = None

        try:
            openapi_bot = injector.get(OpenApiBotPort)
        except Exception as exc:  # noqa: BLE101 未绑定 → fail-closed 占位
            logger.warning(
                "[task_discovery] OpenApiBotPort 未绑定/解析失败(%s: %s) — "
                "SessionInitiator 退化为 UnavailableSessionInitiator"
                "(session 创建 fail-closed, per-bot 容错记录)",
                type(exc).__name__,
                exc,
            )
            return UnavailableSessionInitiator(
                reason=f"OpenApiBotPort DI 解析失败: {type(exc).__name__}: {exc}"
            )
        if openapi_bot is None:
            logger.warning(
                "[task_discovery] OpenApiBotPort resolved to None (fail-closed, "
                "openapi_bot 块未配置或 api_key 缺失) — SessionInitiator 退化为 "
                "UnavailableSessionInitiator"
            )
            return UnavailableSessionInitiator(
                reason="OpenApiBotPort resolved to None（openapi_bot 块未配置/凭证缺失）"
            )

        return OpenApiBotSessionInitiator(
            openapi_bot=openapi_bot,
            frontend_url=_DEFAULT_FRONTEND_URL,
            backend_url=_backend_origin_from_callback_url(bcs_identity_provider),
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
