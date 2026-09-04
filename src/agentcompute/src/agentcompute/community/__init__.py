"""Community layer: SPI contracts, core domain logic, adapters, DI container."""

from . import adapters, bootstrap, core, spi
from ._config import load_dotenv, read_dotenv
from ._logger import get_logger, set_job_id
from ._plugin_registry import (
    LookupError,
    PluginOption,
    PluginRegistry,
    get_registry,
    register_plugin_option,
)

__all__ = [
    "LookupError",
    "PluginOption",
    "PluginRegistry",
    "adapters",
    "bootstrap",
    "core",
    "get_logger",
    "get_registry",
    "load_dotenv",
    "read_dotenv",
    "register_plugin_option",
    "set_job_id",
    "spi",
]
