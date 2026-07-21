"""Tracer — distributed tracing accessors.

The active tracer plugin is lazily discovered via entry points on first
access. Routes call ``get_tracer_plugin()`` to obtain trace IDs for logs;
``app.py`` calls ``install_middleware`` during startup.
"""

from __future__ import annotations

from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.tracer.bare import BareTracerPlugin
from gateway.community.spi.tracer import TracerPlugin

_accessor = PluginAccessor[TracerPlugin]("gateway.tracer", BareTracerPlugin)


def get_tracer_plugin() -> TracerPlugin:
    return _accessor.get()


def set_tracer_plugin(plugin: TracerPlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_tracer_plugin", "set_tracer_plugin"]
