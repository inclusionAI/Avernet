"""Tracer SPI — pluggable tracing abstraction.

Provides the ``TracerPlugin`` protocol.  The active tracer plugin is
managed via ``secbaas.community.tracer``, which discovers the backend
via entry_points and lazily instantiates it on first access.
"""

from ._protocols import TracerPlugin

__all__ = ["TracerPlugin"]
