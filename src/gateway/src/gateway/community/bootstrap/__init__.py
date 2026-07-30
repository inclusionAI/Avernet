"""Bootstrap — dependency injection and application lifecycle.

The composition root: wires concrete plugins and services into the app. Adapters
import the built objects from here (e.g. the ``Authenticator``) rather than
constructing plugins themselves.

``bootstrap_app()`` is the single entry point: it initialises the container,
resolves all plugins, builds the auth + forwarding subsystems, and returns
everything the adapter needs — no individual plugin resolution in app.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dependency_injector.wiring import Provide

from gateway.community.core.authn import Authenticator as _Authn
from gateway.community.core.forwarding import Forwarding as _Fwd

from ._authn import build_authenticator, build_database
from ._configs import DatabaseConfig, init_container_config, load_container_config
from ._container import (
    ApplicationContainer,
    initialize_services,
    shutdown_services,
)
from ._forwarding import build_forwarding

Authenticator = _Authn
Forwarding = _Fwd

_container: ApplicationContainer | None = None


@dataclass(frozen=True)
class BootstrapResult:
    authenticator: Authenticator
    forwarding: Forwarding

    _container: ApplicationContainer = field(repr=False)

    def served_openapi(
        self,
        *,
        title: str,
        version: str,
        description: str = "",
    ) -> dict[str, object]:
        """Return the served OpenAPI document across all configured domains."""
        return self.forwarding.served_openapi(
            self.authenticator.route_security,
            title=title,
            version=version,
            description=description,
        )

    def shutdown(self) -> None:
        import asyncio

        plugins = self._container.plugins()
        db = plugins.database()
        if hasattr(db, "close"):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(db.close())
                else:
                    loop.run_until_complete(db.close())
            except RuntimeError:
                pass
        shutdown_services(self._container)


def bootstrap_app() -> BootstrapResult:
    """Initialise the DI container, resolve plugins, build auth + forwarding.

    This is the single entry point for the adapter layer. ``app.py`` calls
    this once and receives ready-made objects — no individual plugin
    resolution, no builder function calls, no container internals exposed.
    """
    container = get_container()
    init_container_config(container)
    initialize_services(container)

    authenticator = container.authenticator()
    forwarding = container.forwarding()
    return BootstrapResult(
        authenticator=authenticator,
        forwarding=forwarding,
        _container=container,
    )


def _inject_enterprise_plugins(container: ApplicationContainer) -> None:
    try:
        from gateway.community.plugin_registry import (
            has_enterprise_plugins,
            inject_extra_authn_strategies,
            inject_into_plugin_container,
        )

        if has_enterprise_plugins():
            inject_into_plugin_container(container)
            inject_extra_authn_strategies()
    except ImportError:
        pass


def get_container() -> ApplicationContainer:
    global _container
    if _container is None:
        _container = ApplicationContainer()
        _inject_enterprise_plugins(_container)
    return _container


def set_container(container: ApplicationContainer) -> None:
    global _container
    _inject_enterprise_plugins(container)
    _container = container


__all__ = [
    "ApplicationContainer",
    "Authenticator",
    "BootstrapResult",
    "DatabaseConfig",
    "Forwarding",
    "Provide",
    "bootstrap_app",
    "build_authenticator",
    "build_database",
    "build_forwarding",
    "get_container",
    "init_container_config",
    "initialize_services",
    "load_container_config",
    "set_container",
    "shutdown_services",
]
