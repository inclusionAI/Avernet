"""Core-only plugin container — wires open-source-safe plugins.

Use this in the core ``ApplicationContainer`` so that importing
``secbaas.bootstrap`` does not trigger enterprise-only module imports.

Plugin selector strategy:
- **stub** — always available (no external deps)
- **real** — available for open-source backends (desktop, docker, k8s)
- Enterprise-only (buservice, ZCache/ZDAS/DRM, arca_sdk, SOFA tracer/logger/runner)
"""

from dependency_injector import containers, providers

from secbaas.api.device_manage import K8sCredentials
from secbaas.plugins.auth.oauth import OAuthPlugin
from secbaas.plugins.auth.stub import StubAuthPlugin
from secbaas.plugins.bot_service import (
    AiohttpBotServicePlugin,
    LocalBotServicePlugin,
    StubBotServicePlugin,
)
from secbaas.plugins.cache.stub import StubCachePlugin
from secbaas.plugins.database.stub.sqlite_orm import SqliteOrmPlugin
from secbaas.plugins.sandbox.arca import StubArcaSandboxPlugin
from secbaas.plugins.sandbox.arca.local_proc import LocalProcessArcaSandboxPlugin
from secbaas.plugins.sandbox.desktop import (
    RealDesktopSandboxPlugin,
    StubDesktopSandboxPlugin,
)
from secbaas.plugins.sandbox.docker import (
    RealDockerSandboxPlugin,
    StubDockerSandboxPlugin,
)
from secbaas.plugins.sandbox.k8s import RealK8sSandboxPlugin, StubK8sSandboxPlugin
from secbaas.plugins.sandbox.k8s.real import K8sClientManager
from secbaas.plugins.sandbox.poolab import StubPoolabSandboxPlugin
from secbaas.plugins.sandbox.teclaw import StubTeClawBotPlugin
from secbaas.plugins.secret.stub import StubSecretStorePlugin


class PluginContainer(containers.DeclarativeContainer):
    config = providers.Configuration()
    connection_management = providers.Dependency()
    ws_relay_session_repository = providers.Dependency()

    cache_plugin = providers.Selector(
        config.plugins.cache,
        stub=providers.Singleton(StubCachePlugin),
    )

    plugin_database = providers.Selector(
        config.plugins.database.plugin_database,
        SQLITE_ORM=providers.Singleton(SqliteOrmPlugin),
    )

    secret_plugin = providers.Selector(
        config.plugins.secret,
        stub=providers.Singleton(StubSecretStorePlugin),
    )

    auth_plugin = providers.Selector(
        config.plugins.auth,
        oauth=providers.Singleton(OAuthPlugin),
        stub=providers.Singleton(StubAuthPlugin),
    )

    arca_sandbox_plugin_factory = providers.Selector(
        config.plugins.sandbox.arca,
        stub=providers.Object(StubArcaSandboxPlugin),
        local_proc=providers.Object(LocalProcessArcaSandboxPlugin),
    )

    desktop_sandbox_plugin = providers.Selector(
        config.plugins.sandbox.desktop,
        real=providers.Singleton(
            RealDesktopSandboxPlugin,
            connection_manager=connection_management,
        ),
        stub=providers.Singleton(StubDesktopSandboxPlugin),
    )

    teclaw_bot_plugin_factory = providers.Selector(
        config.plugins.sandbox.teclaw,
        stub=providers.Object(StubTeClawBotPlugin),
    )

    k8s_client_manager = providers.Singleton(K8sClientManager)

    k8s_sandbox_plugin_factory = providers.Selector(
        config.plugins.sandbox.k8s,
        real=providers.Singleton(
            RealK8sSandboxPlugin,
            client_manager=k8s_client_manager,
            credentials=providers.Singleton(K8sCredentials),
        ),
        stub=providers.Object(StubK8sSandboxPlugin),
    )

    docker_sandbox_plugin = providers.Selector(
        config.plugins.sandbox.docker,
        real=providers.Singleton(RealDockerSandboxPlugin),
        stub=providers.Singleton(StubDockerSandboxPlugin),
    )

    poolab_sandbox_plugin_factory = providers.Selector(
        config.plugins.sandbox.poolab,
        stub=providers.Object(StubPoolabSandboxPlugin),
    )

    bot_service_plugin = providers.Selector(
        config.plugins.bot_service,
        real=providers.Singleton(AiohttpBotServicePlugin),
        local=providers.Singleton(LocalBotServicePlugin),
        stub=providers.Singleton(StubBotServicePlugin),
    )


__all__ = [
    "PluginContainer",
]
