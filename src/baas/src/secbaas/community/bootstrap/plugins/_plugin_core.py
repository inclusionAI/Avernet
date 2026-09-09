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

import os

from dependency_injector import containers, providers

from secbaas.community.api.device_manage import K8sCredentials
from secbaas.community.plugins.auth.oauth import OAuthPlugin
from secbaas.community.plugins.auth.stub import StubAuthPlugin
from secbaas.community.plugins.bot.teclaw import StubTeClawBotPlugin
from secbaas.community.plugins.bot_service import (
    AiohttpBotServicePlugin,
    LocalBotServicePlugin,
    StubBotServicePlugin,
)
from secbaas.community.plugins.cache.redis import RedisCachePlugin
from secbaas.community.plugins.cache.stub import StubCachePlugin
from secbaas.community.plugins.database.mariadb.mariadb_orm import MariaDbOrmPlugin
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin
from secbaas.community.plugins.eval_env import (
    NoopEvalBindingResolver,
    NoopEvalConsistencyCheck,
    NoopEvalSessionLog,
)
from secbaas.community.plugins.file_transfer import (
    NoopFileTransferBackend,
    NoopSessionFileUrlProjector,
    OssStreamingProxy,
)
from secbaas.community.plugins.file_transfer.aliyun_ack import (
    AliyunAckSessionFileUrlProjector,
)
from secbaas.community.plugins.sandbox.arca import (
    StubArcaSandboxPlugin,
)
from secbaas.community.plugins.sandbox.arca.aliyun_ack import (
    aliyun_ack_plugin_factory,
)
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
from secbaas.community.plugins.sandbox.utils.arca_utils import ArcaUtils
from secbaas.community.plugins.secret.env import EnvSecretStorePlugin
from secbaas.community.plugins.secret.stub import StubSecretStorePlugin


def _read_deploy_tenant(container) -> str:
    """Read ``user_config.env.deploy_tenant`` from the DI Configuration proxy.

    The ``container.config`` Configuration provider exposes sections as
    zero-arg callables (``container.config.env()`` returns the section
    dict); unit tests may pass a plain object whose sections are already
    plain dicts. Both shapes are tolerated — an absent or non-dict env
    section resolves to "" (D-01: missing tenant means the main site).
    """
    env_section = container.config.env
    if callable(env_section):
        env_section = env_section()
    if isinstance(env_section, dict):
        return str(env_section.get("deploy_tenant", "") or "")
    return ""


def _real_file_transfer():
    """Build the community ``real`` file transfer backend (plan 89-03).

    Statement-level replica of the enterprise ``_real_file_transfer``
    factory (A3): the community class-level Selector key makes the
    enterprise runtime injection a silent no-op, so this factory must
    carry both branches — the aliyun tenant reads AK/SK from the
    environment and the main site goes through the secret plugin.
    """
    from secbaas.community.bootstrap import get_container
    from secbaas.community.plugins.file_transfer import (
        AliyunOssFileTransferBackend,
    )

    from .._configs import (
        ConfigError,
        FileTransferOssConfigSchema,
    )

    container = get_container()
    is_aliyun = _read_deploy_tenant(container) == "aliyun"
    section = "file_transfer_oss_aliyun" if is_aliyun else "file_transfer_oss"
    config = getattr(container.config, section)()
    if is_aliyun:
        oss_config = FileTransferOssConfigSchema(**config)
        for field in ("endpoint", "bucket_name", "staging_root_path"):
            if not getattr(oss_config, field):
                raise ConfigError(
                    f"{section}.{field} is required when "
                    f"plugins.file_transfer is 'real'"
                )
        access_key_id = os.environ.get("FT_OSS_ACCESS_KEY")
        access_key_secret = os.environ.get("FT_OSS_SECRET_KEY")
        if not access_key_id or not access_key_secret:
            raise ConfigError(
                f"{section} requires FT_OSS_ACCESS_KEY and "
                f"FT_OSS_SECRET_KEY in the environment when "
                f"plugins.file_transfer is 'real'"
            )
        return AliyunOssFileTransferBackend(
            config=oss_config, credentials=(access_key_id, access_key_secret)
        )

    secret_plugin = container.plugins().secret_plugin()

    oss_config = FileTransferOssConfigSchema(**config)
    for field in (
        "endpoint",
        "bucket_name",
        "secret_name",
        "staging_root_path",
    ):
        if not getattr(oss_config, field):
            raise ConfigError(
                f"{section}.{field} is required when plugins.file_transfer is 'real'"
            )
    return AliyunOssFileTransferBackend(config=oss_config, secret_store=secret_plugin)


class PluginContainer(containers.DeclarativeContainer):
    config = providers.Configuration()
    connection_management = providers.Dependency()
    ws_relay_session_repository = providers.Dependency()

    cache_plugin = providers.Selector(
        config.plugins.cache,
        stub=providers.Singleton(StubCachePlugin),
        redis=providers.Singleton(
            RedisCachePlugin,
            url=config.cache_redis.url,
            socket_timeout=config.cache_redis.socket_timeout,
            socket_connect_timeout=config.cache_redis.socket_connect_timeout,
        ),
    )

    plugin_database = providers.Selector(
        config.plugins.database,
        sqlite=providers.Singleton(
            SqliteOrmPlugin,
            database_url=config.database.database_url,
            create_schema=config.database.create_schema,
            seed_data=config.database.seed_data,
        ),
        mariadb=providers.Singleton(
            MariaDbOrmPlugin,
            database_url=config.database.database_url,
            create_schema=config.database.create_schema,
            seed_data=config.database.seed_data,
        ),
    )

    secret_plugin = providers.Selector(
        config.plugins.secret,
        env=providers.Singleton(EnvSecretStorePlugin),
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
        aliyun_ack=providers.Singleton(
            aliyun_ack_plugin_factory,
            default_images=config.sandbox_images,
            arca_utils=arca_utils,
        ),
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
        config.plugins.bot.teclaw,
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
        real=providers.Singleton(_real_file_transfer),
    )

    session_file_url_projector = providers.Selector(
        config.plugins.session_file_url_projector,
        stub=providers.Singleton(
            NoopSessionFileUrlProjector,
            deploy_tenant=config.env.deploy_tenant,
        ),
        aliyun_ack=providers.Singleton(
            AliyunAckSessionFileUrlProjector,
            proxy_base_url=config.session_file_url_proxy.proxy_base_url,
            deploy_tenant=config.env.deploy_tenant,
        ),
    )

    oss_streaming_proxy = providers.Singleton(
        OssStreamingProxy,
        endpoint=config.file_transfer_oss_aliyun.endpoint,
        bucket_name=config.file_transfer_oss_aliyun.bucket_name,
    )

    eval_binding_resolver = providers.Selector(
        config.plugins.eval_env,
        stub=providers.Singleton(NoopEvalBindingResolver),
    )
    eval_consistency_check = providers.Selector(
        config.plugins.eval_env,
        stub=providers.Singleton(NoopEvalConsistencyCheck),
    )
    eval_session_log = providers.Selector(
        config.plugins.eval_env,
        stub=providers.Singleton(NoopEvalSessionLog),
    )


__all__ = [
    "PluginContainer",
]
