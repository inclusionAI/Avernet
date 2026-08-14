from __future__ import annotations

import os
import threading
from collections.abc import Callable
from importlib.metadata import entry_points


class PluginAccessor[T]:
    """Lazily discover and cache a plugin instance via entry points.

    Args:
        entry_point_group: e.g. ``"gateway.logger"``.
        fallback: callable that returns the default plugin instance used when
            no ``sofa`` implementation is registered (or run mode is ``bare``).
    """

    def __init__(self, entry_point_group: str, fallback: Callable[[], T]) -> None:
        self._group = entry_point_group
        self._fallback = fallback
        self._plugin: T | None = None
        self._lock = threading.Lock()

    def _load(self) -> T:
        is_sofa_mode = os.getenv("GATEWAY_RUN_MODE", "bare").lower() == "sofa"
        if is_sofa_mode:
            for ep in entry_points(group=self._group):
                if ep.name == "sofa":
                    return ep.load()()
        return self._fallback()

    def get(self) -> T:
        if self._plugin is None:
            with self._lock:
                if self._plugin is None:
                    self._plugin = self._load()
        return self._plugin

    def set(self, plugin: T) -> None:
        """Override the cached plugin (useful for tests and explicit wiring)."""
        with self._lock:
            self._plugin = plugin
