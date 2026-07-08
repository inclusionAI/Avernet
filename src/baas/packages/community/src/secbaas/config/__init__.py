"""Configuration loader — loads and validates application config from files.

Provides ``Config`` and ``ConfigLoader`` for environment-driven configuration.
Depends only on ``logger``; consumed by ``bootstrap`` for composition root setup.
"""

from ._config_loader import ConfigLoader
from ._models import Config

__all__ = ["Config", "ConfigLoader"]
