"""Dependency-injection composition root for the engine.

Mirrors ``src/backend/src/agentclaw/di/``. ``container.build_injector`` is the
single place the module list lives; ``runtime_mode.RuntimeConfig`` carries the
mode-as-data that selects testing overrides. Only this package imports
concrete ``engine.plugins`` (once per-engine modules land in F2+); ``core`` and
``api`` receive their dependencies via injection.
"""

from fastapi_injector import Injected

__all__ = ["Injected"]
