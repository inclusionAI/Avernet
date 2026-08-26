"""Database plugin lifecycle wiring for the gateway composition root."""

from __future__ import annotations

from gateway.community.spi.database import DataSourcePlugin


def initialize_database(
    db_plugin: DataSourcePlugin,
) -> DataSourcePlugin:
    """Activate the DI-resolved database plugin and return it.

    The database implementation is selected by ``PluginContainer`` and already
    carries its connection parameters (URL, schema/seed flags) from
    construction. This helper only activates that resolved plugin — mirroring
    the baas ``DatabaseManagerLifecycle.start()`` which calls the no-arg
    ``init_database()`` on the DI-resolved plugin.
    """
    db_plugin.init_database()

    return db_plugin
