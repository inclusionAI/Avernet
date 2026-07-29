from dependency_injector import containers, providers

from gateway.community.plugins.auth.stub import StubAuthPlugin
from gateway.community.plugins.cache.in_memory import InMemoryCachePlugin
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.plugins.forwarder.httpx import HttpxForwarder
from gateway.community.plugins.schema_catalog.file import FileSchemaCatalog


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


__all__ = ["PluginContainer"]
