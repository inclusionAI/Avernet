from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.logger.bare import BareLoggerPlugin
from gateway.community.spi.logger import LoggerPlugin

_accessor = PluginAccessor[LoggerPlugin]("gateway.logger", BareLoggerPlugin)


def get_logger(name: str | None = None):
    """Return a logger for the given name.

    The active logger plugin is lazily discovered via entry points on first
    access, so it can be swapped without a module-level env-var switch.

    Args:
        name: Logger name. If None, an app-level default is used.

    Returns:
        A standard ``logging.Logger`` instance.
    """
    return _accessor.get().get_logger(name)


def get_logger_plugin() -> LoggerPlugin:
    return _accessor.get()


def set_logger_plugin(plugin: LoggerPlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_logger", "get_logger_plugin", "set_logger_plugin"]
