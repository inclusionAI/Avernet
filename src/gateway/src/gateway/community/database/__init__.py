"""Database — data source accessor.

The active database plugin is lazily discovered via entry points on first
access. Community ships a SQLite in-memory backend; enterprise delegates
to ZDAS.
"""

from __future__ import annotations

from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.database.bare import BareDatabasePlugin
from gateway.community.spi.database import DataSourcePlugin

_accessor = PluginAccessor[DataSourcePlugin]("gateway.database", BareDatabasePlugin)


def get_database_plugin() -> DataSourcePlugin:
    return _accessor.get()


def set_database_plugin(plugin: DataSourcePlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_database_plugin", "set_database_plugin"]
