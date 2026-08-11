from dependency_injector import containers, providers

from secbaas.community.api.publish_manage import PublishService
from secbaas.community.core.service.paas import DeviceCallbackHandler
from secbaas.community.core.service.paas.desktop import ConnectionManager
from secbaas.community.logger import get_logger

from ._configs import ConfigError, ConfigKey, DatabaseConfig, _read_config
from ._core_repository import CoreRepositoryContainer
from ._core_services import CoreServiceContainer
from ._core_tasks import CoreTaskContainer
from ._cron import CronLifecycle
from ._lifecycle import (
    DatabaseManagerLifecycle,
    Lifecycle,
    LocalProcessManagerLifecycle,
)


def _lazy_publish_service() -> PublishService:
    """Resolve the publish_service from the live ApplicationContainer services.

    Referenced lazily (at device-callback time) to break the
    ``device_service ↔ publish_service`` object-graph cycle: publish service is
    only resolved when a callback actually fires, not during container resolution.
    Uses the live container singleton via ``get_container()`` so config/Selector
    overrides applied at bootstrap are visible.
    """
    from secbaas.community.bootstrap import get_container

    return get_container().services.publish_service()


# Enterprise registers extra plugin options via plugin_registry at import
# time. PluginContainer reads from the registry to build its Selectors, so
# no enterprise import is needed here.
from .plugins import PluginContainer  # noqa: E402

logger = get_logger("bootstrap")


def _build_db_config(config) -> DatabaseConfig:
    """Construct DatabaseConfig from the DI Configuration provider."""
    from secbaas.community.spi.database import PluginDatabaseType

    plugin_type = PluginDatabaseType(_read_config(config, ConfigKey.PLUGIN_DATABASE))
    try:
        db_url = _read_config(config, ConfigKey.DATABASE_URL)
    except ConfigError:
        if plugin_type == PluginDatabaseType.SQLITE_ORM:
            raise
        db_url = ""

    def _opt(key: ConfigKey) -> str:
        try:
            return _read_config(config, key)
        except ConfigError:
            return ""

    def _opt_bool_default_false(key: ConfigKey, default: bool = False) -> bool:
        try:
            val = _read_config(config, key)
        except ConfigError:
            return default
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in {"1", "true", "yes", "on"}

    return DatabaseConfig(
        plugin_type=plugin_type,
        db_url=db_url,
        create_schema=_opt_bool_default_false(ConfigKey.CREATE_SCHEMA),
        seed_data=_opt_bool_default_false(ConfigKey.SEED_DATA),
        mariadb_host=_opt(ConfigKey.MARIADB_HOST) or "127.0.0.1",
        mariadb_port=int(_opt(ConfigKey.MARIADB_PORT) or 3306),
        mariadb_database=_opt(ConfigKey.MARIADB_DATABASE),
        mariadb_user=_opt(ConfigKey.MARIADB_USER),
        mariadb_password=_opt(ConfigKey.MARIADB_PASSWORD),
    )


def _provider_label(provider) -> str:
    """Human-readable label: ``Singleton → DefaultPublishAdminService``."""
    label = type(provider).__name__
    cls = getattr(provider, "cls", None)
    if cls:
        return f"{label} → {cls.__name__}"
    provides = getattr(provider, "provides", None)
    if provides is not None and provides is not provider:
        if isinstance(provides, type):
            return f"{label} → {provides.__name__}"
        return f"{label} → {provides!r}"
    return label


def _render_provider_tree(
    container: containers.DeclarativeContainer, indent: str = ""
) -> list[str]:
    """Recursively render provider name → type lines."""
    lines = []
    for name, provider in container.providers.items():
        if isinstance(provider, providers.Container):
            sub = provider()
            lines.append(f"{indent}  {name}: Container")
            lines.extend(_render_provider_tree(sub, indent + "    "))
        elif isinstance(provider, providers.Configuration):
            lines.append(f"{indent}  {name}: Configuration")
        elif isinstance(provider, providers.Dependency):
            lines.append(f"{indent}  {name}: Dependency")
        elif isinstance(provider, providers.Selector):
            lines.append(f"{indent}  {name}: Selector")
        else:
            lines.append(f"{indent}  {name}: {_provider_label(provider)}")
    return lines


def _log_container_components(container: containers.DeclarativeContainer) -> None:
    """Log all registered provider names, types, and provided class names."""
    lines = _render_provider_tree(container)
    if lines:
        logger.info("Container components:\n%s", "\n".join(lines))


class ApplicationContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    repository = providers.Container(CoreRepositoryContainer, config=config)

    connection_management = providers.Singleton(
        ConnectionManager,
        repository=repository.local_user_machine_repository,
    )

    plugins = providers.Container(
        PluginContainer,
        config=config,
        connection_management=connection_management,
        ws_relay_session_repository=repository.ws_relay_session_repository,
    )

    services = providers.Container(
        CoreServiceContainer,
        config=config,
        secret_plugin=plugins.secret_plugin,
        auth_plugin=plugins.auth_plugin,
        arca_sandbox_plugin_factory=plugins.arca_sandbox_plugin_factory,
        desktop_sandbox_plugin=plugins.desktop_sandbox_plugin,
        poolab_sandbox_plugin_factory=plugins.poolab_sandbox_plugin_factory,
        teclaw_bot_plugin_factory=plugins.teclaw_bot_plugin_factory,
        k8s_sandbox_plugin_factory=plugins.k8s_sandbox_plugin_factory,
        docker_sandbox_plugin=plugins.docker_sandbox_plugin,
        k8s_client_manager=plugins.k8s_client_manager,
        connection_management=connection_management,
        bot_repo=repository.bot_repository,
        device_repo=repository.device_repository,
        ac_bot_repo=repository.ac_bot_repository,
        ac_bot_publish_repo=repository.ac_bot_publish_repository,
        device_binding_repo=repository.device_binding_repository,
        api_gateway_repo=repository.api_gateway_repository,
        bot_device_rel_repo=repository.bot_device_rel_repository,
        bot_session_repo=repository.bot_session_repository,
        publish_repo=repository.publish_repository,
        publish_batch_repo=repository.publish_batch_repository,
        publish_record_repo=repository.publish_record_repository,
        tenant_repo=repository.tenant_repository,
        system_config_repo=repository.system_config_repository,
        device_template_repo=repository.device_template_repository,
        local_user_machine_repo=repository.local_user_machine_repository,
        bot_run_repository=repository.bot_run_repository,
        bot_run_queue_repository=repository.bot_run_queue_repository,
        bot_run_queue_chunk_repository=repository.bot_run_queue_chunk_repository,
        bot_qpm_repository=repository.bot_qpm_repository,
        distributed_lock_repository=repository.distributed_lock_repository,
        cache_plugin=plugins.cache_plugin,
        file_transfer_backend=plugins.file_transfer_backend,
        ws_relay_session_repo=repository.ws_relay_session_repository,
        ticket_repository=repository.ticket_repository,
        session_ticket_repository=repository.session_ticket_repository,
        device_callback_handler=providers.Singleton(
            DeviceCallbackHandler,
            publish_service_factory=_lazy_publish_service,
        ),
    )

    tasks = providers.Container(
        CoreTaskContainer,
        config=config,
        distributed_lock_service=services.distributed_lock_service,
        device_binding_repo=repository.device_binding_repository,
        sandbox_device_router=services.sandbox_device_router,
        bot_run_queue_repository=repository.bot_run_queue_repository,
        ticket_repository=repository.ticket_repository,
        paas_service_facade=services.paas_facade,
        file_transfer_backend=services.file_transfer_backend,
    )

    cron_lifecycle = providers.Singleton(
        CronLifecycle,
        app_scheduler=services.app_scheduler,
        tasks=providers.List(
            tasks.device_ttl_timer_task,
            tasks.bot_run_recovery_task,
            tasks.file_transfer_poller_task,
        ),
    )

    # ── Database config (resolved lazily, used by DatabaseManagerLifecycle) ──
    db_config = providers.Singleton(
        _build_db_config,
        config=config,
    )

    # ── Lifecycle-ordered component list ─────────────────────────────────────
    # Start order: DatabaseManager → ConnectionManager → InstanceRouter →
    #   WorkerRouter → CronLifecycle → BotRequestWorker → LocalProcessManager.
    # Stop order: reverse of above.
    lifecycle_components = providers.List(
        providers.Singleton(DatabaseManagerLifecycle, db_config=db_config),
        services.connection_management,
        services.instance_router,
        services.worker_router,
        cron_lifecycle,
        services.bot_request_worker,
        providers.Singleton(LocalProcessManagerLifecycle),
    )


def _resolve_all_providers(container: containers.DeclarativeContainer) -> None:
    """Recursively resolve all providers in a container tree.

    Walks all sub-containers and calls ``provider()`` on every
    Singleton / Factory / Selector to eagerly materialise them.
    ``Configuration`` and ``Dependency`` providers are skipped (calling
    them has no effect).
    """
    for name, provider in container.providers.items():
        if isinstance(provider, (providers.Configuration, providers.Dependency)):
            continue
        if isinstance(provider, providers.Container):
            sub = provider()
            _resolve_all_providers(sub)
            continue
        try:
            provider()
        except Exception as e:
            logger.error("  failed to resolve %s: %s", name, e)
            raise


async def initialize_services(container: containers.DeclarativeContainer) -> None:
    """Eagerly resolve all components and start lifecycle-aware services.

    Resolution order: plugins → repository → services → tasks.
    Then starts all ``Lifecycle`` components in the order defined by
    ``container.lifecycle_components`` (first is DatabaseManager which
    initialises the database engine).
    """
    logger.info("Wiring web routers")
    container.wire(packages=["secbaas.community.adapters.web"])

    _log_config_summary(container)
    _log_container_components(container)

    logger.info("Resolving container.plugins …")
    _resolve_all_providers(container.plugins())

    logger.info("Resolving container.repository …")
    _resolve_all_providers(container.repository())

    logger.info("Resolving container.services …")
    _resolve_all_providers(container.services())

    logger.info("Resolving container.tasks …")
    _resolve_all_providers(container.tasks())

    # ── Start lifecycle components in order ─────────────────────────────────
    # First component (DatabaseManagerLifecycle) initialises the database;
    # subsequent components start desktop infra, cron, worker, etc.
    components: list[Lifecycle] = list(container.lifecycle_components())
    for component in components:
        name = type(component).__name__
        try:
            await component.start()
            logger.info("%s started", name)
        except Exception as e:
            logger.error("%s start failed: %s", name, e)
            raise RuntimeError(f"Failed to start {name}") from e

    logger.info("All components initialised successfully")


async def shutdown_services(container: containers.DeclarativeContainer) -> None:
    """Gracefully shut down all lifecycle-aware services.

    Stops all ``Lifecycle`` components in reverse order of
    ``container.lifecycle_components``.  Each component's ``stop()`` is
    called even if a preceding component fails.
    """
    components: list[Lifecycle] = list(container.lifecycle_components())
    for component in reversed(components):
        name = type(component).__name__
        try:
            await component.stop()
            logger.info("%s stopped", name)
        except Exception as e:
            logger.error("%s stop failed: %s", name, e)

    logger.info("All components shut down")


def _log_config_summary(container: containers.DeclarativeContainer) -> None:
    """Log a summary of resolved config values at startup."""
    import json

    config_dict = container.config()
    if not config_dict:
        logger.info("Container config: <empty>")
        return
    formatted = json.dumps(config_dict, indent=2, default=str, ensure_ascii=False)
    logger.info("Container config:\n%s", formatted)


def _inject_enterprise_plugins(container: ApplicationContainer) -> None:
    """Inject enterprise plugin options into a container instance's Selectors.

    Runs at instance level (not class level) so that only this container
    is affected.  Called after every container creation.
    """
    try:
        from secbaas.community.plugin_registry import (
            has_enterprise_plugins,
            inject_into_plugin_container,
        )

        if has_enterprise_plugins():
            inject_into_plugin_container(container)
    except ImportError:
        pass
