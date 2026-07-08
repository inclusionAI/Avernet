"""
Process-wide :class:`EngineRegistry` singleton.

The registry is intentionally global because engines self-register at
*import* time (see ``engines/<name>/__init__.py::_self_register``) long
before anything constructs an :class:`engine.community.manager.EngineManager`.
Attaching the registry to the manager would force import ordering that
the test harness and the cron sidecars can't guarantee, so instead the
registry lives here and the manager *consults* it via
:meth:`engine.community.manager.EngineManager.get_registered_engines`.

Design notes
------------
* One instance per process — importers get the same registry whether they
  call :func:`get_registry` from the manager, an engine package, or a
  test fixture.
* :func:`reset_registry` wipes the singleton for tests. Production code
  should never call it; re-import is idempotent thanks to
  :meth:`EngineRegistry.register`'s same-class no-op behaviour.
* Registration is best-effort at import time: the package's
  ``_self_register`` wraps this call in try/except so an unexpected
  registry failure can't take a fresh venv down mid-import.
"""
from __future__ import annotations

from engine.community.core.engine.registry import DEFAULT_REGISTRY, EngineRegistry

# Alias to the single module-level registry exported by ``registry.py``.
# Historically this module had its own private ``_REGISTRY`` instance, but
# keeping two instances causes split-brain registration (engines registered
# via ``DEFAULT_REGISTRY.register`` are invisible to ``get_registry()`` and
# vice versa). Both names now point at the same object so ``_self_register``
# paths stay compatible with either convention.
_REGISTRY: EngineRegistry = DEFAULT_REGISTRY


def get_registry() -> EngineRegistry:
    """Return the process-wide :class:`EngineRegistry` instance."""
    return _REGISTRY


def reset_registry() -> None:
    """Clear every registration on the process-wide registry (tests only).

    Production code should not call this — it breaks engines that have
    already been registered at import time. Test suites that need a
    clean slate (e.g. to re-run ``_self_register`` under patched env
    vars) can call this in a fixture teardown and then reimport the
    engine package.
    """
    _REGISTRY.clear()


__all__ = ["get_registry", "reset_registry"]
