"""Database plugin lifecycle wiring for the gateway composition root."""

from __future__ import annotations

from gateway.community.spi.database import DataSourcePlugin

from ._configs import DatabaseConfig


def initialize_database(
    db_plugin: DataSourcePlugin,
    config: DatabaseConfig,
) -> DataSourcePlugin:
    """Initialise the DI-resolved database plugin and return it.

    The database implementation is selected by ``PluginContainer``. This helper
    only applies the already-loaded configuration to that resolved plugin and
    seeds bare-mode data; it never constructs a concrete database implementation
    itself.
    """
    db_plugin.init_database(config)

    # Seed bare-mode authn rows after schema creation. The plugin's own
    # ``seed`` is a no-op because it cannot import core ORM models (layer rule:
    # plugins must not import core). The composition root has no such ban.

    return db_plugin
