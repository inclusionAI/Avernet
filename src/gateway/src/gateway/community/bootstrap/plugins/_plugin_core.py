from typing import Any

from dependency_injector import containers, providers

from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.core.app import AppRepository
from gateway.community.core.bot import BotRepository
from gateway.community.plugins.auth.stub import StubAuthPlugin
from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.plugins.authn.app_token import AppTokenStrategy
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.plugins.cache.in_memory import InMemoryCachePlugin
from gateway.community.plugins.cache.redis import RedisCacheConfig, RedisCachePlugin
from gateway.community.plugins.database.mariadb import MariaDbOrmPlugin
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.plugins.forwarder.httpx import HttpxForwarder
from gateway.community.plugins.schema_catalog.file import FileSchemaCatalog
from gateway.community.plugins.schema_catalog.http import HttpSchemaCatalog
from gateway.community.plugins.secret_resolver.community import CommunitySecretResolver
from gateway.community.plugins.secret_resolver.env import EnvSecretResolver
from gateway.community.spi.secret_resolver import SecretResolver


def _default(value, fallback):
    return value if value not in (None, "") else fallback


def _secret_value(material: object) -> str:
    """Read the plaintext secret from either SecretResolver return shape.

    ``get_secret`` returns either a plain ``str`` (the BaaS-aligned ``env``
    flavor) or a duck-typed object exposing ``.secret_value``/``.secret_user``
    (the ``community`` flavor), or ``None``. Normalise to the plain string.
    """
    if material is None:
        return ""
    if isinstance(material, str):
        return material
    return getattr(material, "secret_value", "") or ""


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
            try:
                material = secret_resolver.get_secret(value[1:])
            except RuntimeError as exc:
                raise ValueError(
                    f"Unresolvable secret reference {value!r} for config field {key!r}"
                ) from exc
            if material is None:
                raise ValueError(
                    f"Unresolvable secret reference {value!r} for config field {key!r}"
                )
            resolved[key] = _secret_value(material)
    return resolved


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
        sqlite=providers.Singleton(
            SqliteDatabasePlugin,
            database_url=config.plugins.database.database_url,
            create_schema=config.plugins.database.create_schema,
            seed_data=config.plugins.database.seed_data,
        ),
        mariadb=providers.Singleton(
            MariaDbOrmPlugin,
            database_url=config.plugins.database.database_url,
            create_schema=config.plugins.database.create_schema,
            seed_data=config.plugins.database.seed_data,
        ),
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
    # from the process environment; the ``env`` flavor provides a BaaS-aligned
    # env-backed resolver (BaaS ``EnvSecretStorePlugin`` contract). Enterprise
    # may register further options via plugin_registry.
    secret_resolver = providers.Selector(
        config.plugins.secret,
        community=providers.Singleton(
            CommunitySecretResolver, env_prefix=config.secret.env_prefix
        ),
        env=providers.Singleton(EnvSecretResolver, env_prefix=config.secret.env_prefix),
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

    authn_strategies = providers.Dict(
        google=google_strategy,
        bot_token=bot_token_strategy,
        app_token=app_token_strategy,
        access_key_token=access_key_token_strategy,
    )


__all__ = ["PluginContainer"]
