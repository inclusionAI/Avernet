"""BotDormantModule — production singletons for the dormant-bot subsystem.

Bindings registered here:
  - BaasDormantClient   — HTTP client to BaaS health-checker (env-driven base_url)
  - DormantBotService   — scan-and-decision orchestrator
  - ActivateBotService  — reactivates RECYCLED bots
  - RecycleBotService   — recycles ACTIVE personal managed-cloud bots
  - WhitelistService    — batch-adds bots to the dormant whitelist
  - DormantBotLifecycle — single-cron lifecycle participant (auto-discovered by
                          discover_lifecycle_participants via LifecycleBase)

DormantBotService.__init__ takes ``bot_service: BotServiceProtocol`` as a
forward reference (TYPE_CHECKING guard) that injector cannot resolve by type
annotation alone.  We use a ``@provider`` factory to pass the already-bound
BotServiceProtocol explicitly.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable

from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
    BotDormantAuditServiceProtocol,
    BotDormantRecycleOperationProtocol,
    BotDormantRecycleServiceProtocol,
)
from agentclaw.community.api.bot_service import (
    BotServiceProtocol as _ApiBotServiceProtocol,
)
from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService
from agentclaw.community.core.bot_dormant.audit_service import DormantAuditService
from agentclaw.community.core.bot_dormant.baas_client import BaasDormantClient
from agentclaw.community.core.bot_dormant.internal_service import DormantInternalService
from agentclaw.community.core.bot_dormant.lifecycle import DormantBotLifecycle
from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.core.bot_dormant.protocols import (
    BotServiceProtocol as _DormantBotServiceProtocol,
)
from agentclaw.community.core.bot_dormant.recycle_operation import (
    RecycleHandler,
    RecycleOperationService,
    RecycleTaskLifecycle,
)
from agentclaw.community.core.bot_dormant.recycle_runtime import DeviceRecycleRuntime
from agentclaw.community.core.bot_dormant.recycle_service import RecycleBotService
from agentclaw.community.core.bot_dormant.recycle_state import RecycleUnavailable
from agentclaw.community.core.bot_dormant.scan_policy import DormantScanPolicyService
from agentclaw.community.core.bot_dormant.service import DormantBotService
from agentclaw.community.core.bot_dormant.whitelist_service import WhitelistService
from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import (
    RestartBackupDependencies,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError as BotManagementNotFoundError,
)
from agentclaw.community.core.bot_startup_script.protocols import (
    TeclawEngineTestProtocol,
)
from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.repository.implementations.bot.recycle import (
    RecycleRepository,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    BotRestartLockRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.recycle import (
    BotRecycleRepositoryProtocol,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.di.config import (
    DormantConfig,
    DormantInternalToken,
    DormantNotifyConfig,
    SecretNamesConfig,
    TaskQueueConfig,
    TaskQueueWorkerConfig,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.env_utils import get_current_env
from injector import Binder, Injector, Module, inject, provider, singleton

logger = get_logger()

# The Mist secret name backing the internal Bearer token is deployment config
# (``SecretNamesConfig.dormant_internal_token``, from the ``secret_names`` yaml
# block) — corp env overlays set the real name; the neutral shipped code carries
# no secret-registry reference (OSS-0 #3). An empty name (community / singlebox /
# test) short-circuits to the local fallback token below.

# Fallback token used when SecretResolver returns None (singlebox / CI /
# Mist unreachable). Lets local联调 still call /api/internal/dormant/*
# without provisioning a real Mist secret. Matches the pattern in
# core/skill_center/services/skill_scan.py (fallback_secret_value).
# NOTE: this is intentionally a publicly-visible string — it only ever
# gates singlebox/local where no real authority decision is at stake.
_SINGLEBOX_FALLBACK_TOKEN = "singlebox-dormant-token-local"


class _DormantBotServiceAdapter:
    """Normalize BotService behavior to the dormant module's local contract."""

    def __init__(self, bot_service: _ApiBotServiceProtocol) -> None:
        self._bot_service = bot_service

    def get_bot(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._bot_service.get_bot(*args, **kwargs)
        except BotManagementNotFoundError:
            return None

    def update_status(self, *args: Any, **kwargs: Any) -> Any:
        return self._bot_service.update_status(*args, **kwargs)

    def stop_bot(self, *args: Any, **kwargs: Any) -> Any:
        return self._bot_service.stop_bot(*args, **kwargs)

    def start_bot(self, *args: Any, **kwargs: Any) -> Any:
        return self._bot_service.start_bot(*args, **kwargs)

    def is_teclaw_bot(self, active_engine: str | None) -> bool:
        return self._bot_service.is_teclaw_bot(active_engine)


class BotDormantModule(Module):
    """Production bindings for the dormant-bot subsystem."""

    def configure(self, binder: Binder) -> None:
        # Simple singletons with @inject constructors — injector resolves all deps.
        # BaasDormantClient now takes Annotated[HttpClient, QUALIFIER_BAAS] via
        # @inject (bound by InfrastructureModule), so injector wires base_url
        # transitively via the qualified HttpClient — no explicit provider needed.
        binder.bind(BaasDormantClient, to=BaasDormantClient, scope=singleton)
        binder.bind(WhitelistService, to=WhitelistService, scope=singleton)
        binder.bind(DormantInternalService, to=DormantInternalService, scope=singleton)
        binder.bind(DormantScanPolicyService, to=DormantScanPolicyService, scope=singleton)
        binder.bind(RecycleBotService, to=RecycleBotService, scope=singleton)
        binder.bind(DormantAuditService, to=DormantAuditService, scope=singleton)

    @singleton
    @provider
    @inject
    def _activate_bot_service(
        self,
        bot_service: _DormantBotServiceProtocol,
        passport_plugin: PassportPlugin,
        store: BotRecycleRepositoryProtocol,
    ) -> ActivateBotService:
        """Construct ActivateBotService with the passport plugin explicitly wired."""
        return ActivateBotService(
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            activation_control=store,
        )

    @singleton
    @provider
    @inject
    def _activate_bot_service_protocol(
        self, service: ActivateBotService
    ) -> BotDormantActivateServiceProtocol:
        """Bind the public activate Protocol to the concrete service."""
        return service

    @singleton
    @provider
    @inject
    def _recycle_bot_service_protocol(
        self, service: RecycleBotService
    ) -> BotDormantRecycleServiceProtocol:
        """Bind the public recycle Protocol to the shared Core service."""
        return service

    @singleton
    @provider
    @inject
    def recycle_store(
        self,
        db: DatabasePlugin,
        config: TaskQueueConfig,
        engine_test: Callable[[], TeclawEngineTestProtocol],
    ) -> BotRecycleRepositoryProtocol:
        return RecycleRepository(
            db, config.app, is_teclaw=lambda engine: engine_test().is_teclaw(engine)
        )

    @singleton
    @provider
    @inject
    def recycle_operations(
        self,
        store: BotRecycleRepositoryProtocol,
        bots: BotRepository,
        queue: TaskQueueService,
        locks: BotRestartLockRepositoryProtocol,
        dependencies: RestartBackupDependencies,
        config: TaskQueueConfig,
        worker: TaskQueueWorkerConfig,
        injector: Injector,
        engine_test: Callable[[], TeclawEngineTestProtocol],
    ) -> RecycleOperationService:
        @contextmanager
        def guard(bot):
            env, entity = get_current_env(), bot.get("entity_id")
            if not entity:
                raise RecycleUnavailable("Bot lock identity unavailable")
            lock = locks.acquire(
                env, entity, bot["bot_id"], "recycle:" + bot["owner_id"]
            )
            if lock is None:
                # Admission/receipt verification only: the durable journal, not
                # this short TTL, protects a long-running operation.
                stale = locks.get_if_stale(env, entity, bot["bot_id"], 120)
                # Never reap a legacy release/restart lock: provider calls may
                # outlive 120s without a durable recycle journal protecting them.
                if stale is not None and stale.holder_user_id.startswith("recycle:"):
                    locks.release(env, entity, bot["bot_id"], stale.lock_token)
                    lock = locks.acquire(
                        env, entity, bot["bot_id"], "recycle:" + bot["owner_id"]
                    )
            if lock is None:
                from agentclaw.community.core.bot_management.services.bot_service import (
                    BotInvalidLifecycleStateError,
                )

                raise BotInvalidLifecycleStateError(
                    bot_id=bot["bot_id"], current_status="BUSY"
                )
            try:
                yield
            finally:
                locks.release(env, entity, bot["bot_id"], lock.lock_token)

        return RecycleOperationService(
            store=store,
            queue=queue,
            guard=guard,
            app=config.app,
            worker_enabled=worker.enabled,
            is_teclaw=lambda engine: engine_test().is_teclaw(engine),
            runtime=DeviceRecycleRuntime(
                lambda: injector.get(DeviceService), bots, dependencies
            ),
        )

    @singleton
    @provider
    @inject
    def recycle_operation_protocol(
        self, service: RecycleOperationService
    ) -> BotDormantRecycleOperationProtocol:
        return service

    @singleton
    @provider
    @inject
    def recycle_task_lifecycle(
        self,
        service: RecycleOperationService,
        registry: HandlerRegistry,
        passport: PassportPlugin,
    ) -> RecycleTaskLifecycle:
        return RecycleTaskLifecycle(registry, RecycleHandler(service, passport))

    @singleton
    @provider
    @inject
    def _dormant_audit_service_protocol(
        self, service: DormantAuditService
    ) -> BotDormantAuditServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def _dormant_ops_service(
        self,
        dormant_service: DormantBotService,
        passport_plugin: PassportPlugin,
        recycle_service: RecycleBotService,
    ) -> DormantOpsService:
        return DormantOpsService(
            dormant_service=dormant_service,
            passport_plugin=passport_plugin,
            recycle_service=recycle_service,
        )

    @singleton
    @provider
    @inject
    def _bridge_bot_service_protocol(
        self,
        bot_service: _ApiBotServiceProtocol,
    ) -> _DormantBotServiceProtocol:
        """Adapt the API service to the dormant module's local contract."""
        return _DormantBotServiceAdapter(bot_service)

    @singleton
    @provider
    @inject
    def _dormant_bot_service(
        self,
        db: DatabasePlugin,
        baas_client: BaasDormantClient,
        bot_service: _DormantBotServiceProtocol,
        recycle_service: RecycleBotService,
        scan_policy: DormantScanPolicyService,
        common_whitelist_service: CommonWhiteListService,
        config: DormantConfig,
        notify_config: DormantNotifyConfig,
    ) -> DormantBotService:
        """Construct DormantBotService with explicit BotServiceProtocol injection.

        Inject DormantScanPolicyService so is_dry_run() reads ac_common_config
        at runtime instead of the legacy YAML/env knob.
        """
        return DormantBotService(
            db=db,
            baas_client=baas_client,
            bot_service=bot_service,
            recycle_service=recycle_service,
            scan_policy=scan_policy,
            common_whitelist_service=common_whitelist_service,
            dry_run=config.dry_run,
            action_link_pattern=notify_config.action_link_pattern,
        )

    @singleton
    @provider
    @inject
    def _resolved_dormant_token(
        self,
        secret_resolver: SecretResolver,
        secret_names: SecretNamesConfig,
    ) -> DormantInternalToken:
        """Resolve the internal Bearer token via SecretResolver.

        The secret name comes from ``SecretNamesConfig.dormant_internal_token``
        (the ``secret_names`` yaml block; Mist via layotto in prod / pre).

        Resolution rules:
          - name is empty (community / singlebox / test — no corp secret name)
              → ``.value = _SINGLEBOX_FALLBACK_TOKEN`` (local fallback, so本地
                联调可调接口; unchanged from the previous None-resolver path)
          - Mist returns a secret with non-empty ``secret_value``
              → ``.value = secret.secret_value`` (prod / pre normal path)
          - Mist returns ``None`` or empty ``secret_value``
              → ``.value = _SINGLEBOX_FALLBACK_TOKEN``
          - resolver raises (transient Mist outage / network)
              → ``.value = ""`` (failure-closed: 401 all requests,
                 don't authorize garbage)
        """
        secret_name = secret_names.dormant_internal_token
        if not secret_name:
            logger.info(
                "[bot_dormant_module] no dormant token secret name configured — "
                "local fallback token in use"
            )
            return DormantInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        try:
            secret = secret_resolver.get_secret(secret_name=secret_name)
        except Exception:
            logger.exception(
                "[bot_dormant_module] SecretResolver.get_secret failed for %r — "
                "returning empty token (failure-closed)",
                secret_name,
            )
            return DormantInternalToken(value="")

        if secret is None:
            logger.info(
                "[bot_dormant_module] SecretResolver returned None for %r — "
                "singlebox/local fallback token in use",
                secret_name,
            )
            return DormantInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        value = getattr(secret, "secret_value", None)
        if not value:
            logger.warning(
                "[bot_dormant_module] secret %r resolved but secret_value "
                "empty — falling back to singlebox token",
                secret_name,
            )
            return DormantInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        return DormantInternalToken(value=str(value))

    @singleton
    @provider
    @inject
    def _dormant_bot_lifecycle(
        self,
        service: DormantBotService,
        cache: CachePlugin,
        scan_policy: DormantScanPolicyService,
    ) -> DormantBotLifecycle:
        """Construct DormantBotLifecycle — picked up by discover_lifecycle_participants."""
        return DormantBotLifecycle(
            service=service,
            cache=cache,
            scan_policy=scan_policy,
        )
