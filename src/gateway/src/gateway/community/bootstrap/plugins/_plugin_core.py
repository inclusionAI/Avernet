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
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.plugins.forwarder.httpx import HttpxForwarder
from gateway.community.plugins.schema_catalog.file import FileSchemaCatalog


def _default(value, fallback):
    return value if value not in (None, "") else fallback


class PluginContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    database = providers.Selector(
        config.plugins.database.plugin_database,
        SQLITE_ORM=providers.Singleton(SqliteDatabasePlugin),
    )

    forwarder = providers.Selector(
        config.plugins.forwarder,
        httpx=providers.Singleton(HttpxForwarder),
    )

    schema_catalog = providers.Selector(
        config.plugins.schema_catalog,
        file=providers.Singleton(FileSchemaCatalog),
    )

    cache_plugin = providers.Selector(
        config.plugins.cache,
        stub=providers.Singleton(InMemoryCachePlugin),
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
        default_tenant=providers.Callable(
            _default, config.authn.google.default_tenant, "default"
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
