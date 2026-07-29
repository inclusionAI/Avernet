from dependency_injector import containers, providers

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

    container.authenticator.override(
        providers.Singleton(
            build_authenticator,
            db=plugins.providers["database"],
            app_token_validator=plugins.providers["app_token_validator"],
            tenant_resolver=plugins.providers["tenant_resolver"],
        )
    )

    logger.info("Building forwarding …")
    from ._forwarding import build_forwarding

    container.forwarding.override(
        providers.Singleton(
            build_forwarding,
            forwarder=plugins.providers["forwarder"],
            catalog=plugins.providers["schema_catalog"],
        )
    )

    logger.info("All components initialised successfully")


def shutdown_services(container: containers.DeclarativeContainer) -> None:
    import logging

    logger = logging.getLogger("bootstrap")
    logger.info("All components shut down")
