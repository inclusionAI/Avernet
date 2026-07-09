"""Tracer SPI — pluggable tracing abstraction.

Provides the ``TracerPlugin`` protocol that allows the tracing backend
to be selected at module init time.  Callers import ``get_tracer_plugin``;
the composition root (bootstrap) is responsible for calling
``set_tracer_plugin`` once before any consumer requests a tracer.
"""

from ._protocols import TracerPlugin

_tracer_plugin: TracerPlugin | None = None


def set_tracer_plugin(plugin: TracerPlugin) -> None:
    """Register the tracer plugin (called once by bootstrap / composition root)."""
    global _tracer_plugin
    _tracer_plugin = plugin


def get_tracer_plugin() -> TracerPlugin:
    """Return the current tracer plugin instance.

    Returns ``"-"`` when no active span exists.
    """
    if _tracer_plugin is None:
        raise RuntimeError(
            "TracerPlugin has not been initialised — "
            "call set_tracer_plugin() during startup."
        )
    return _tracer_plugin


__all__ = ["TracerPlugin", "get_tracer_plugin", "set_tracer_plugin"]
