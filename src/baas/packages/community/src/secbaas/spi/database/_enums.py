"""Plugin database identifiers for repository backend selection.

Set via `plugins.database.plugin_database` in application.yaml
or the `PLUGIN_DATABASE` environment variable.
"""

from enum import StrEnum


class PluginDatabaseType(StrEnum):
    """Repository backend plugin identifiers."""

    ZDAS_ORM = "ZDAS_ORM"
    SQLITE_ORM = "SQLITE_ORM"