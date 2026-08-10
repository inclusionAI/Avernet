"""Core-only plugin container — wires open-source-safe plugins.

Use this in the core ``ApplicationContainer`` so that importing
``secbaas.community.bootstrap`` does not trigger enterprise-only module imports.

Plugin selector strategy:
- **stub** — always available (no external deps)
- **real** — available for open-source backends (desktop, docker, k8s)
- Enterprise-only options are injected via ``plugin_registry`` at runtime

Enterprise registers extra selector options (e.g. cache.real, auth.buservice)
by calling ``register_plugin_option()`` at import time.
``inject_into_plugin_container()`` merges them into each container instance's
Selectors via the public ``set_providers()`` API, so no enterprise import is
needed here and class-level Selectors stay untouched.
"""

from dependency_injector import containers, providers

from secbaas.community.api.device_manage import K8sCredentials
from secbaas.community.plugins.auth.oauth import OAuthPlugin
from secbaas.community.plugins.auth.stub import StubAuthPlugin
from secbaas.community.plugins.bot_service import (
    AiohttpBotServicePlugin,
    LocalBotServicePlugin,
    StubBotServicePlugin,
)
from secbaas.community.plugins.cache.stub import StubCachePlugin
from secbaas.community.plugins.database.mariadb.mariadb_orm import MariaDbOrmPlugin
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin
from secbaas.community.plugins.file_transfer import NoopFileTransferBackend
from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin
from secbaas.community.plugins.sandbox.arca.local_proc import (
    LocalProcessArcaSandboxPlugin,
)
from secbaas.community.plugins.sandbox.desktop import (
    RealDesktopSandboxPlugin,
    StubDesktopSandboxPlugin,
)
from secbaas.community.plugins.sandbox.docker import (
    RealDockerSandboxPlugin,
    StubDockerSandboxPlugin,
)
from secbaas.community.plugins.sandbox.k8s import (
    RealK8sSandboxPlugin,
    StubK8sSandboxPlugin,
)
from secbaas.community.plugins.sandbox.k8s.real import K8sClientManager
from secbaas.community.plugins.sandbox.poolab import StubPoolabSandboxPlugin
from secbaas.community.plugins.sandbox.teclaw import StubTeClawBotPlugin
from secbaas.community.plugins.sandbox.utils.arca_utils import ArcaUtils
from secbaas.community.plugins.secret.stub import StubSecretStorePlugin


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
        MARIADB_ORM=providers.Singleton(MariaDbOrmPlugin),
    )

    secret_plugin = providers.Selector(
        config.plugins.secret,
        stub=providers.Singleton(StubSecretStorePlugin),
    )

    arca_utils = providers.Singleton(
        ArcaUtils,
        secret_plugin=secret_plugin,
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
            arca_utils=arca_utils,
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

    file_transfer_backend = providers.Selector(
        config.plugins.file_transfer,
        stub=providers.Singleton(NoopFileTransferBackend),
    )


__all__ = [
    "PluginContainer",
]
