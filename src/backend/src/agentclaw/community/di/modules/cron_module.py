"""CronModule — production singleton for the cron module.

Replaces the lazy module-global ``_cron_relay_service`` in
``core/cron/dependencies/cron.py`` with an injector ``@singleton``
self-binding. ``CronRelayService.__init__`` has ``@inject`` and
takes ``BotService`` + ``DeviceService`` (which structurally satisfy
the cron Protocols), so a ``configure(binder)`` self-binding is
enough.

Note (per spec): cron tasks themselves run *outside* HTTP requests,
so request-scoped bindings (e.g. ``OperatorContext`` via
``Depends(get_operator_context)``) are **not** available there.
Cron tasks must build any per-call context manually. This module
only governs the routes that drive the cron system from the HTTP
side — those are normal request-scoped FastAPI handlers.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.cron.services.cron_auto_setup_listener import (
    CronAutoSetupListener,
)
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.di.config import CronGuardInternalToken, SecretNamesConfig
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


class CronModule(Module):
    """Production bindings for the cron module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(CronRelayService, to=CronRelayService, scope=singleton)
        # The HTTP-to-adapter boundary (``DeviceAdapterTransport``) is a device
        # concern, bound per-profile by the device column (B6 T26): corp →
        # ``CorpDevicesModule`` (HttpDeviceAdapterTransport); test →
        # ``TestingDevicesModule`` (in-memory under pytest / real under
        # singlebox); community leaves it unbound (no container runtime).
        # Lifecycle participant — subscribes to DeviceActivatedEvent in startup()
        binder.bind(CronAutoSetupListener, to=CronAutoSetupListener, scope=singleton)

    @singleton
    @provider
    @inject
    def _cron_relay_service_protocol(self, svc: CronRelayService) -> CronRelayServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _cron_guard_internal_token(
        self,
        secret_resolver: SecretResolver,
        secret_names: SecretNamesConfig,
    ) -> CronGuardInternalToken:
        """Missing name, missing value, or resolver failure all disable writes."""
        if not secret_names.cron_guard_internal_token:
            return CronGuardInternalToken()
        try:
            resolved_record = secret_resolver.get_secret(
                secret_name=secret_names.cron_guard_internal_token,
            )
        except Exception:
            return CronGuardInternalToken()
        value = getattr(resolved_record, "secret_value", None)
        return CronGuardInternalToken(value=str(value) if value else "")
