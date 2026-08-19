from dependency_injector import containers, providers

from ._configs import ConfigError, ConfigKey, DatabaseConfig, _read_config
from .plugins import PluginContainer


def _provider_label(provider) -> str:
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
    lines = []
    for name, provider in container.providers.items():
        if isinstance(provider, providers.Container):
            sub = provider()
            lines.append(f"{indent}  {name}: Container")
            lines.extend(_render_provider_tree(sub, indent + "    "))
        elif isinstance(provider, providers.Configuration):
            lines.append(f"{indent}  {name}: Configuration")
        elif isinstance(provider, providers.Dependency):
            try:
                resolved = provider()
                lines.append(
                    f"{indent}  {name}: Dependency → {type(resolved).__name__}"
                )
            except Exception:
                lines.append(f"{indent}  {name}: Dependency (unresolved)")
        elif isinstance(provider, providers.Selector):
            try:
                resolved = provider()
                lines.append(f"{indent}  {name}: Selector → {type(resolved).__name__}")
            except Exception:
                lines.append(f"{indent}  {name}: Selector (unresolved)")
        elif isinstance(provider, providers.Singleton):
            try:
                resolved = provider()
                lines.append(f"{indent}  {name}: Singleton → {type(resolved).__name__}")
            except Exception:
                lines.append(f"{indent}  {name}: Singleton (unresolved)")
        elif isinstance(provider, providers.Callable):
            try:
                resolved = provider()
                if isinstance(resolved, dict):
                    items = ", ".join(
                        f"{k}: {type(v).__name__}" for k, v in resolved.items()
                    )
                    lines.append(f"{indent}  {name}: Callable → {{{items}}}")
                else:
                    lines.append(
                        f"{indent}  {name}: Callable → {type(resolved).__name__}"
                    )
            except Exception:
                lines.append(f"{indent}  {name}: Callable (unresolved)")
        else:
            lines.append(f"{indent}  {name}: {_provider_label(provider)}")
    return lines


def _log_container_components(container: containers.DeclarativeContainer) -> None:
    from gateway.community.logger import get_logger

    logger = get_logger("bootstrap")
    lines = _render_provider_tree(container)
    if lines:
        logger.info("Container components:\n%s", "\n".join(lines))


class ApplicationContainer(containers.DeclarativeContainer):
    config = providers.Configuration()
    loaded_config = providers.Dependency()
    user_config = providers.Dependency()
    plugins = providers.Container(PluginContainer, config=config)

    authenticator = providers.Dependency()
    forwarding = providers.Dependency()


def _resolve_all_providers(container: containers.DeclarativeContainer) -> None:
    import logging

    logger = logging.getLogger("bootstrap")
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


def _build_db_config(config) -> DatabaseConfig:
    """Construct DatabaseConfig from the DI ``config`` provider.

    Mirrors the baas ``_build_db_config``: reads the dotted database paths via
    ``_read_config``, raises for a missing URL on SQLite_ORM, and otherwise
    falls back to baas-compatible defaults. Credentials are never logged.
    """
    from gateway.community.spi.database import PluginDatabaseType

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
        plugin_type=plugin_type.value,
        db_url=db_url,
        create_schema=_opt_bool_default_false(ConfigKey.CREATE_SCHEMA),
        seed_data=_opt_bool_default_false(ConfigKey.SEED_DATA),
        mariadb_host=_opt(ConfigKey.MARIADB_HOST) or "127.0.0.1",
        mariadb_port=int(_opt(ConfigKey.MARIADB_PORT) or 3306),
        mariadb_database=_opt(ConfigKey.MARIADB_DATABASE),
        mariadb_user=_opt(ConfigKey.MARIADB_USER),
        mariadb_password=_opt(ConfigKey.MARIADB_PASSWORD),
    )


def initialize_services(container: containers.DeclarativeContainer) -> None:
    import logging

    logger = logging.getLogger("bootstrap")
    logger.info("Wiring web routers")
    container.wire(packages=["gateway.community.adapters.web"])

    _log_container_components(container)

    logger.info("Resolving container.plugins …")
    _resolve_all_providers(container.plugins())

    logger.info("Building authenticator …")
    plugins = container.plugins()
    from ._authn import build_authenticator
    from ._database import initialize_database

    # Initialise the DI-selected database plugin so DB-backed auth strategies
    # and credential issuer/registrar share one ready DataSourcePlugin.
    initialize_database(
        plugins.database(),
        _build_db_config(container.config),
    )

    container.authenticator.override(
        providers.Singleton(
            build_authenticator,
            strategies=plugins.providers["authn_strategies"],
            user_config=container.user_config,
        )
    )

    logger.info("Building forwarding …")
    from gateway.community.adapters.web import WebsocketsForwarder

    from ._forwarding import build_forwarding

    container.forwarding.override(
        providers.Singleton(
            build_forwarding,
            forwarder=plugins.providers["forwarder"],
            schema_catalogs=plugins.providers["schema_catalogs"],
            ws_forwarder=providers.Singleton(WebsocketsForwarder),
        )
    )

    logger.info("All components initialised successfully")


def _inject_enterprise_plugins(container: ApplicationContainer) -> None:
    try:
        from gateway.community.plugin_registry import (
            has_enterprise_plugins,
            inject_into_plugin_container,
        )

        if has_enterprise_plugins():
            inject_into_plugin_container(container)
    except ImportError:
        pass


def shutdown_services(container: containers.DeclarativeContainer) -> None:
    import logging

    logger = logging.getLogger("bootstrap")
    logger.info("All components shut down")
