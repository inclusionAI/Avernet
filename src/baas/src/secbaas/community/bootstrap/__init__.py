"""Dependency injection container hierarchy.

Provides centralized DI management for the application layer:
- `plugins`: PluginContainer — 7 SPI plugins with config-driven real/stub selection
- `repository`: RepositoryContainer — DatabaseManager + 17 repos with ORM/ZDAS selection
- `services`: CoreServiceContainer — All services + infrastructure providers
- `container`: ApplicationContainer — Top-level assembly, wires sub-containers

Usage in FastAPI routes:
    from dependency_injector.wiring import Provide
    from secbaas.community.bootstrap import ApplicationContainer

    @router.get(...)
    async def handler(
        service: SomeService = Depends(Provide[ApplicationContainer.services.some_service]),
    ):
        ...
"""

from dependency_injector.wiring import Provide

from ._configs import DatabaseConfig, init_container_config, load_container_config
from ._container import (
    ApplicationContainer,
    initialize_services,
    shutdown_services,
)

# Module-level singleton: created once, reused by all _factory.py delegates
_container: ApplicationContainer | None = None


def get_container() -> ApplicationContainer:
    global _container
    if _container is None:
        _container = ApplicationContainer()
        from ._container import _inject_enterprise_plugins

        _inject_enterprise_plugins(_container)
    return _container


def set_container(container: ApplicationContainer) -> None:
    """Set the module-level container singleton.

    Called by app.py's lifespan after creating and config-populating a container.
    """
    global _container
    from ._container import _inject_enterprise_plugins

    _inject_enterprise_plugins(container)
    _container = container


__all__ = [
    "ApplicationContainer",
    "DatabaseConfig",
    "get_container",
    "init_container_config",
    "initialize_services",
    "load_container_config",
    "set_container",
    "shutdown_services",
    "Provide",
]
