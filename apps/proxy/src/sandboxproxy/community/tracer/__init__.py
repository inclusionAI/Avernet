"""Tracer accessor — lazily resolve the tracer plugin via entry points."""

from __future__ import annotations

from typing import Any

from sandboxproxy.community.plugin_accessor import PluginAccessor


def _fallback_tracer() -> Any:
    from sandboxproxy.community.plugins.tracer.bare import BareTracerPlugin

    return BareTracerPlugin()


_tracer_accessor: PluginAccessor[Any] = PluginAccessor(
    "sandboxproxy.tracer", _fallback_tracer
)


def get_tracer_plugin() -> Any:
    return _tracer_accessor.get()
