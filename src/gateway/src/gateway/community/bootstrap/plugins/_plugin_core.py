from typing import Any

from dependency_injector import containers, providers

from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.core.app import AppRepository
from gateway.community.core.bot import BotRepository
from gateway.community.plugins.auth.stub import StubAuthPlugin
from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.plugins.authn.app_token import AppTokenStrategy
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.dev_cookie import DevCookieUserStrategy
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.plugins.cache.in_memory import InMemoryCachePlugin
from gateway.community.plugins.cache.redis import RedisCacheConfig, RedisCachePlugin
from gateway.community.plugins.database.mariadb import MariaDbOrmPlugin
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.plugins.forwarder.httpx import HttpxForwarder
from gateway.community.plugins.schema_catalog.file import FileSchemaCatalog
from gateway.community.plugins.schema_catalog.http import HttpSchemaCatalog
from gateway.community.plugins.secret_resolver.community import CommunitySecretResolver
from gateway.community.plugins.secret_resolver.kms import (
    AliyunKmsSecretResolver,
    KmsSecretResolverConfig,
)
from gateway.community.spi.secret_resolver import SecretResolver


def _default(value, fallback):
    return value if value not in (None, "") else fallback


def _resolve_secret_refs(
    raw_config: dict[str, Any], secret_resolver: SecretResolver
) -> dict[str, Any]:
    """Resolve ``@name`` secret references in opaque config via a SecretResolver.

    Sensitive connection fields (e.g. ``password``) may carry a secret reference
    (``@name``). The composition root resolves those references before
    constructing the client so the plugin never runs with a raw reference.
    Unresolvable references raise explicitly rather than starting an
    empty-valued connection.
    """
    resolved = dict(raw_config)
    for key, value in resolved.items():
        if isinstance(value, str) and value.startswith("@"):
            material = secret_resolver.get_secret(value[1:])
            if material is None:
                raise ValueError(
                    f"Unresolvable secret reference {value!r} for config field {key!r}"
                )
            resolved[key] = getattr(material, "secret_value", "") or ""
    return resolved


def _build_kms_config(
    raw_config: dict[str, Any], secret_resolver: SecretResolver
) -> KmsSecretResolverConfig:
    """Build a KMS resolver config, resolving secret references up front.

    The KMS resolver's own access credentials are resolved via the passed
    ``SecretResolver`` (the community/env resolver acting as the bootstrap
    credential source for reaching the managed store).

    Returns:
        A :class:`KmsSecretResolverConfig` whose sensitive fields carry concrete
        values, never secret references.
    """
    resolved = _resolve_secret_refs(raw_config, secret_resolver)
    return KmsSecretResolverConfig(**resolved)


def _build_redis_config(
    raw_config: dict[str, Any], secret_resolver: SecretResolver
) -> RedisCacheConfig:
    """Build a redis plugin config, resolving secret references up front.

    Returns:
        A :class:`RedisCacheConfig` whose sensitive fields carry concrete
        values, never secret references.
    """
    resolved = _resolve_secret_refs(raw_config, secret_resolver)
    return RedisCacheConfig(**resolved)


class PluginContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    database = providers.Selector(
        config.plugins.database.plugin_database,
        SQLITE_ORM=providers.Singleton(SqliteDatabasePlugin),
        MARIADB_ORM=providers.Singleton(MariaDbOrmPlugin),
    )

    forwarder = providers.Selector(
        config.plugins.forwarder,
        httpx=providers.Singleton(HttpxForwarder),
    )

    schema_catalogs = providers.Dict(
        file=providers.Singleton(FileSchemaCatalog),
        http=providers.Singleton(HttpSchemaCatalog),
    )

    # SecretResolver — community flavor reads signing keys (and other creds)
    # from the process environment; ``aliyun_kms`` resolves them from Aliyun KMS
    # and resolves its own access credentials (AK id/secret) via secret
    # references from the community/env resolver acting as the bootstrap source.
    # Enterprise may register further options via plugin_registry.
    secret_resolver = providers.Selector(
        config.plugins.secret,
        community=providers.Singleton(
            CommunitySecretResolver, env_prefix=config.secret.env_prefix
        ),
        aliyun_kms=providers.Singleton(
            AliyunKmsSecretResolver,
            config=providers.Callable(
                _build_kms_config,
                raw_config=config.plugins.secret_aliyun_kms,
                secret_resolver=providers.Singleton(
                    CommunitySecretResolver, env_prefix=config.secret.env_prefix
                ),
            ),
        ),
    )

    cache_plugin = providers.Selector(
        config.plugins.cache,
        stub=providers.Singleton(InMemoryCachePlugin),
        redis=providers.Singleton(
            RedisCachePlugin,
            config=providers.Callable(
                _build_redis_config,
                raw_config=config.plugins.cache_redis,
                secret_resolver=secret_resolver,
            ),
        ),
    )

    auth = providers.Selector(
        config.plugins.auth,
        stub=providers.Singleton(StubAuthPlugin),
    )

    bot_registry = providers.Factory(BotRepository, db=database)
    app_registry = providers.Factory(AppRepository, db=database)
    access_key_registry = providers.Factory(AccessKeyRepository, db=database)

    google_strategy = providers.Singleton(
        GoogleUserStrategy,
        token_header=providers.Callable(
            _default, config.authn.google.token_header, "x-google-token"
        ),
        userinfo_url=providers.Callable(
            _default,
            config.authn.google.userinfo_url,
            "https://openidconnect.googleapis.com/v1/userinfo",
        ),
    )
    dev_cookie_strategy = providers.Singleton(DevCookieUserStrategy)

    bot_token_strategy = providers.Singleton(
        BotTokenStrategy,
        registry=bot_registry,
        token_header=providers.Callable(
            _default, config.authn.bot_token.token_header, "x-avernet-bot-token"
        ),
    )
    app_token_strategy = providers.Singleton(
        AppTokenStrategy,
        registry=app_registry,
        token_header=providers.Callable(
            _default,
            config.authn.app_token_strategy.token_header,
            "x-avernet-app-token",
        ),
    )
    access_key_token_strategy = providers.Singleton(
        AccessKeyTokenStrategy,
        registry=access_key_registry,
        token_header=providers.Callable(
            _default,
            config.authn.access_key_token.token_header,
            "x-avernet-access-key-token",
        ),
    )

    # The dev auth mock (plugins/authn/dev_header) is deliberately NOT in this
    # pool: this container is the production DI graph — enterprise merges its
    # strategies into this same dict — and the mock must not exist in it. The
    # bootstrap constructs it directly, only under GATEWAY_AUTH_MOCK=1
    # (bootstrap/_authn.py), so without the env var it is never even imported.
    authn_strategies = providers.Dict(
        google=google_strategy,
        dev_cookie=dev_cookie_strategy,
        bot_token=bot_token_strategy,
        app_token=app_token_strategy,
        access_key_token=access_key_token_strategy,
    )


__all__ = ["PluginContainer"]
