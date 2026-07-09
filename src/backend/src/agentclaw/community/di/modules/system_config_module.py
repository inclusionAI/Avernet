"""SystemConfigModule — production singletons for system_config.

Replaces the duplicated factory shims in
``core/system_config/dependencies/config_dep.py``
(``get_config_repository`` / ``get_config_service`` /
``get_device_config_service``), all of which mode-branched on
``DATABASE_MODE`` and re-instantiated services per request.

Three services share one ``ConfigRepositoryProtocol`` here; each
service is bound as a ``@singleton @provider`` so all callers (routes
plus the transitional shims in ``core/devices/dependencies/device_dep.py``)
share the same instance.

``ConfigRepositoryProtocol`` is now a single unified ORM body
(``plugins/config_repository.py``) that runs on both OceanBase
prod and SQLite via the injected DatabasePlugin — no SQLite override
module needed. No mode branching here.
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from injector import inject, provider

from agentclaw.community.api.device_config_service import DeviceConfigServiceProtocol
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.core.system_config import (
    ClusterConfigService,
    ConfigRepositoryProtocol,
    DeviceConfigService,
    SystemConfigService,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.config_repository import ConfigRepository


logger = get_logger()


class SystemConfigModule(Module):
    """Production bindings for system_config."""

    def configure(self, binder: Binder) -> None:
        # All three services have ``@inject`` ctors taking already-bound
        # deps (``ConfigRepositoryProtocol`` is supplied below; the two
        # derived services take ``SystemConfigService``).
        binder.bind(SystemConfigService, to=SystemConfigService, scope=singleton)
        binder.bind(DeviceConfigService, to=DeviceConfigService, scope=singleton)
        binder.bind(ClusterConfigService, to=ClusterConfigService, scope=singleton)
        # Single unified ORM impl — runs on prod OceanBase and SQLite
        # via the injected DatabasePlugin (@inject ctor).
        binder.bind(
            ConfigRepositoryProtocol,
            to=ConfigRepository,
            scope=singleton,
        )

    # Bind the Service API Protocols to the same singletons so HTTP routers
    # (and any future non-HTTP adapter) can resolve `Injected(<X>Protocol)`
    # and reach the concrete service without naming it directly.
    @singleton
    @provider
    @inject
    def _system_config_service_protocol(
        self, svc: SystemConfigService
    ) -> SystemConfigServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _device_config_service_protocol(
        self, svc: DeviceConfigService
    ) -> DeviceConfigServiceProtocol:
        return svc
