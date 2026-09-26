"""ManagerModule — binds :class:`EngineManager` as a singleton.

The provider injects :class:`EngineConfig` and constructs the manager with the
**startup default** engine name (``config.default_engine``); the active engine
*instance* is created later by the manager's async ``initialize()``. The
``engine.engines`` import side-effect-registers every bundled engine with
``DEFAULT_REGISTRY`` before construction, mirroring
``EngineManager.get_instance()``.

The manager is a singleton because there is exactly **one** active engine per
process — but it owns the *current* engine as mutable instance state
(``_engine``/``_active_engine``), which ``switch()`` mutates. So singleton scope
is correct and not in tension with runtime switching: the config supplies only
the boot default; the live value lives on the (single, shared) manager.

F1 note: production still resolves the manager via ``EngineManager.get_instance()``
until the composition root lands (Task 22); this binding makes the injector the
authoritative source so that switch happens cleanly in Task 22.
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from engine.community.config import EngineConfig
from engine.community.manager import EngineManager
from engine.community.plugin_api.notification.protocol import NotificationService


class ManagerModule(Module):
    """Production binding for :class:`EngineManager`."""

    @singleton
    @provider
    @inject
    def engine_manager(
        self,
        config: EngineConfig,
        notification_service: NotificationService,
    ) -> EngineManager:
        import engine.community.engines  # noqa: F401 — ensure engines self-register

        return EngineManager(
            config.default_engine,
            notification_service=notification_service,
        )
