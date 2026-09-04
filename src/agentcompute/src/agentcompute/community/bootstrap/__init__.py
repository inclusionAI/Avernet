"""DI container and bootstrap — mirrors ``secbaas.community.bootstrap``.

``get_container()`` returns the application container whose ``plugins()``
accessor resolves plugin instances by config-driven key lookups into the
plugin registry. Community owns this; ``community.plugins`` only registers
options.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from .._plugin_registry import get_registry
from ..spi._agent import Agent
from ..spi._database import DatabasePlugin
from ..spi._driver import Driver
from ..spi._llm import LLMProvider
from ..spi._logger import LoggerPlugin
from ..spi._planner import Planner
from ..spi._replanner import Replanner
from ..spi._tracer import TracerPlugin
from ._yaml_config import ApplicationConfig, detect_env, load_config

__all__ = [
    "ApplicationConfig",
    "Config",
    "Container",
    "PluginAccessor",
    "detect_env",
    "get_config",
    "get_container",
    "load_config",
    "set_config",
]


@dataclass
class Config:
    """Runtime configuration: which plugin option each SPI uses."""

    llm_provider: str = "stub"
    agent_factory: str = "llm"
    logger: str = "stdlib"
    tracer: str = "stdlib"
    database: str = "sqlite"
    planner: str = "static"
    driver: str = "static"
    replanner: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


class PluginAccessor:
    """Resolves plugin instances through the registry using the config."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def llm_provider(self) -> LLMProvider:
        return cast(LLMProvider, self._resolve("llm_provider", self._config.llm_provider))

    def agent(self, name: str) -> Agent:
        return cast(Agent, self._resolve("agent", name))

    def logger(self) -> LoggerPlugin:
        return cast(LoggerPlugin, self._resolve("logger", self._config.logger))

    def tracer(self) -> TracerPlugin:
        return cast(TracerPlugin, self._resolve("tracer", self._config.tracer))

    def database(self) -> DatabasePlugin:
        return cast(DatabasePlugin, self._resolve("database", self._config.database))

    def planner(self) -> Planner:
        return cast(Planner, self._resolve("planner", self._config.planner))

    def driver(self) -> Driver:
        return cast(Driver, self._resolve("driver", self._config.driver))

    def replanner(self) -> Replanner | None:
        name = self._config.replanner
        if name is None:
            return None
        return cast(Replanner, self._resolve("replanner", name))

    def registered_agents(self) -> list[str]:
        return get_registry().names("agent")

    def _resolve(self, key: str, name: str) -> Any:
        return get_registry().get(key, name).factory()


@dataclass
class Container:
    config: Config
    _plugins: PluginAccessor = field(init=False)

    def __post_init__(self) -> None:
        self._plugins = PluginAccessor(self.config)

    def plugins(self) -> PluginAccessor:
        return self._plugins


_container: Container | None = None
_config: Config | None = None


def set_config(config: Config) -> None:
    global _config, _container
    _config = config
    _container = Container(config)


def get_config() -> Config:
    if _config is None:
        set_config(Config())
    assert _config is not None
    return _config


def get_container() -> Container:
    if _container is None:
        set_config(_config if _config is not None else Config())
    assert _container is not None
    return _container
