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
from gateway.community.plugins.cache.redis import RedisCachePlugin
from gateway.community.plugins.database.mariadb import MariaDbOrmPlugin
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.plugins.forwarder.httpx import HttpxForwarder
from gateway.community.plugins.schema_catalog.file import FileSchemaCatalog
from gateway.community.plugins.schema_catalog.http import HttpSchemaCatalog
from gateway.community.plugins.secret_resolver.community import CommunitySecretResolver


def _default(value, fallback):
    return value if value not in (None, "") else fallback


class PluginContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    database = providers.Selector(
        config.plugins.database,
        sqlite=providers.Singleton(
            SqliteDatabasePlugin,
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

    forwarder = providers.Selector(
        config.plugins.forwarder,
        httpx=providers.Singleton(HttpxForwarder),
    )

    schema_catalogs = providers.Dict(
        file=providers.Singleton(FileSchemaCatalog),
        http=providers.Singleton(HttpSchemaCatalog),
    )

    # SecretResolver — community flavor reads signing keys (and other creds)
    # from the process environment. Enterprise may register further options via
    # plugin_registry.
    secret_resolver = providers.Selector(
        config.plugins.secret,
        community=providers.Singleton(
            CommunitySecretResolver,
        ),
    )

    cache_plugin = providers.Selector(
        config.plugins.cache,
        stub=providers.Singleton(InMemoryCachePlugin),
        redis=providers.Singleton(
            RedisCachePlugin,
            url=config.cache_redis.url,
            socket_timeout=config.cache_redis.socket_timeout,
            socket_connect_timeout=config.cache_redis.socket_connect_timeout,
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
