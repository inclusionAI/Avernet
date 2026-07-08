"""Re-export core plugin container as the default container.

In community mode, PluginContainer is the core container with stub/bare plugins.
When secbaas-enterprise is installed, enterprise/bootstrap/_plugin_enterprise.py
extends this with real implementations.
"""

from ._plugin_core import PluginContainer

__all__ = [
    "PluginContainer",
]
