"""Cron task registry — deferred factory registration surface for extensions.

Extensions (enterprise or other bundles) register deferred factories at
import time. Community's CronLifecycle task assembly reads this registry
lazily at container resolution time — never at import or container
class-body time, because extension modules import after the bootstrap
class body has already executed (same timing as plugin_registry and
router_registry).

Two independent surfaces live here:

- Cron task factories, keyed by stable name. Each name maps to at most
  one factory (last registration wins). The bootstrap container looks
  up a name via ``providers.Callable`` and calls the factory only when
  the task list is resolved.
- Device service factories, an ordered list. The bootstrap container
  applies each registered factory as a device-service provider override
  at container injection time (last registration wins).

Factories are deferred callables — not dependency_injector providers —
so that extension module imports only happen when the factory is
actually called.
"""

from __future__ import annotations

from collections.abc import Callable

_task_factories: dict[str, Callable[[], object]] = {}
_device_service_factories: list[Callable[[], object]] = []


def register_cron_task_factory(name: str, factory: Callable[[], object]) -> None:
    """Register a deferred cron task factory by stable name.

    Called by extensions at import time. The factory is deferred so that
    extension module imports only happen at container resolution time.
    """
    _task_factories[name] = factory


def get_cron_task_factories() -> dict[str, Callable[[], object]]:
    """Return a snapshot copy of registered task factories.

    Callers must read this at container resolution time (via
    ``providers.Callable``), never at import / class-body time —
    extensions register after the bootstrap class body executes.
    """
    return dict(_task_factories)


def register_device_service_factory(factory: Callable[[], object]) -> None:
    """Register a device service factory overriding the default service.

    Appended in registration order. The bootstrap container applies each
    registered factory as a device-service provider override at container
    injection time (last registration wins).
    """
    _device_service_factories.append(factory)


def get_device_service_factories() -> list[Callable[[], object]]:
    """Return a snapshot copy of registered device service factories."""
    return list(_device_service_factories)
