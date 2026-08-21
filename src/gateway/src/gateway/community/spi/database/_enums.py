"""Plugin database identifiers for repository backend selection.

Set via ``plugins.database`` in application.yaml
or the ``PLUGIN_DATABASE`` environment variable.
"""

from enum import StrEnum


class PluginDatabaseType(StrEnum):
    """Repository backend plugin identifiers."""

    zdas = "zdas"
    sqlite = "sqlite"
    mariadb = "mariadb"
