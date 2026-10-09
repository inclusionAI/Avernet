"""Tracer — distributed tracing factory.

The active tracer plugin is lazily discovered via entry_points on first
access, so it can be swapped without a module-level env-var switch.

Usage::

    from secbaas.community.tracer import get_tracer_plugin

    tracer = get_tracer_plugin()
    tracer.setup("secbaas")
    tracer.install_middleware(app)
"""

from __future__ import annotations

from secbaas.community.plugin_accessor import PluginAccessor
from secbaas.community.plugins.tracer.bare import BareTracerPlugin
from secbaas.community.spi.tracer import TracerPlugin

_accessor = PluginAccessor[TracerPlugin]("secbaas.tracer", BareTracerPlugin)


def get_tracer_plugin() -> TracerPlugin:
    return _accessor.get()


def set_tracer_plugin(plugin: TracerPlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_tracer_plugin", "set_tracer_plugin"]
