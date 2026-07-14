"""Lazy plugin accessor — shared pattern for entry-point-based plugin discovery.

Logger and Tracer both follow the same pattern: discover a plugin via
``entry_points`` when ``SECBAAS_RUN_MODE=sofa``, fall back to a bare
implementation, and cache the result.  This module factors out that
boilerplate.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from importlib.metadata import entry_points


class PluginAccessor[T]:
    """Lazily discover and cache a plugin instance via entry_points.

    Args:
        entry_point_group: e.g. ``"secbaas.logger"``
        fallback: callable that returns the default plugin instance.
    """

    def __init__(self, entry_point_group: str, fallback: Callable[[], T]) -> None:
        self._group = entry_point_group
        self._fallback = fallback
        self._plugin: T | None = None

    def _load(self) -> T:
        is_sofa_mode = os.getenv("SECBAAS_RUN_MODE", "bare").lower() == "sofa"
        if is_sofa_mode:
            for ep in entry_points(group=self._group):
                if ep.name == "sofa":
                    return ep.load()()
        return self._fallback()

    def get(self) -> T:
        if self._plugin is None:
            self._plugin = self._load()
        return self._plugin

    def set(self, plugin: T) -> None:
        self._plugin = plugin
