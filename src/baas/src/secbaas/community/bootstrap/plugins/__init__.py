"""Re-export core plugin container as the default container.

In community mode, PluginContainer is the core container with stub/bare plugins.
When the enterprise bundle is installed, its bootstrap module extends this with
real implementations.
"""

from ._plugin_core import PluginContainer

__all__ = [
    "PluginContainer",
]
