"""Community DeviceSync dispatcher wiring.

Community devices are BaaS-backed. The module binds only the Plugin dispatcher
and injects a factory for the shared Core ``DeviceSync`` services.

Two binding-routed providers land here, matching
``device_context_resolver._BINDING_ROUTED_PROVIDERS``:

* ``baas`` → :class:`BaasDeviceSyncService` — per-domain push over the
  invoke-http transport.
* ``teclaw`` → :class:`TeclawDeviceSyncService` — whole-artifact delivery: it
  re-composes the bot's full ``BotConfigArtifact`` and POSTs it to the running
  container. Shares the BaaS runtime (``get_bind_id`` / ``get_http_info``) but
  not the per-domain transport.

The teclaw branch resolves ``ConfigComposer`` / ``BotPublishService`` /
``HttpClient[general]`` lazily through the ``Injector`` rather than taking them
as provider params: the composer's graph is large and the device-sync
dispatcher is itself a dependency of skill-center and MCP services, so eager
injection here risks closing a DI cycle at graph-build time. Same cycle-break
as ``BotManagementModule.bot_publish_service_factory``.
"""
from __future__ import annotations

from typing import Annotated, Any, Callable

from injector import Injector, Module, inject, provider, singleton

from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.devices.services.teclaw_device_sync import (
    TeclawDeviceSyncService,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_GENERAL

logger = get_logger()


class CommunityDeviceSyncModule(Module):
    """Bind the community dispatcher, optionally adapting its BaaS Core service."""

    def __init__(
        self,
        device_sync_wrapper: Callable[[DeviceSync], DeviceSync] | None = None,
    ) -> None:
        self._device_sync_wrapper = device_sync_wrapper

    @singleton
    @provider
    @inject
    def device_sync_dispatcher(
        self,
        baas_service: BaasService,
        injector: Injector,
    ) -> DeviceSyncDispatcher:
        from agentclaw.community.plugins.community.device_sync_dispatcher import (
            CommunityDeviceSyncDispatcher,
        )

        def baas_device_sync(ctx: DeviceContext) -> DeviceSync:
            conn_info = ctx.conn_info
            transport = BaasInvokeTransport(
                bind_id=conn_info["bind_id"],
                engine_port=conn_info["engine_port"],
                tenant=conn_info.get("tenant", ""),
                baas_service=baas_service,
                device_uuid=conn_info.get("device_uuid"),
            )
            service: DeviceSync = BaasDeviceSyncService(
                transport=transport,
                conn_info=conn_info,
            )
            if self._device_sync_wrapper is not None:
                service = self._device_sync_wrapper(service)
            return service

        def teclaw_device_sync(ctx: DeviceContext) -> DeviceSync:
            # Imported inside the factory (not at module import) so this DI
            # module stays cheap to import and the composer graph is only
            # touched when a teclaw bot is actually dispatched.
            from agentclaw.community.core.config_compose.services.config_composer import (
                ConfigComposer,
            )
            from agentclaw.community.core.service_bot.services.bot_publish_service import (
                BotPublishService,
            )

            # The bot row carries the identity fields the composer and the
            # binding lookup need. ``entity_id`` (compose scope) and
            # ``owner_id`` (binding lookup) are deliberately kept apart — see
            # the TeclawDeviceSyncService module docstring.
            bot = _lookup_bot(injector, ctx.bot_id)
            # NOTE: ``device_sync_wrapper`` is intentionally NOT applied here.
            # The singlebox wrapper exists to defer ``sync_all_mcp_servers``
            # because the engine serves ``/api/mcp/filter-servers`` through the
            # mcporter CLI — a per-domain BaaS concern. Teclaw never issues that
            # call (it re-delivers the whole artifact), so wrapping would drop a
            # legitimate push.
            return TeclawDeviceSyncService(
                conn_info=ctx.conn_info,
                bot_id=ctx.bot_id,
                bot_name=bot.get("bot_name", ""),
                user_id=ctx.user_id,
                owner_id=bot.get("owner_id") or ctx.user_id,
                entity_id=bot.get("entity_id") or None,
                engine_type=bot.get("active_engine") or TECLAW_DEVICE_PROVIDER,
                entity_type=bot.get("entity_type") or "staff",
                composer_provider=lambda: injector.get(ConfigComposer),
                baas_service=baas_service,
                http_client=injector.get(Annotated[HttpClient, QUALIFIER_GENERAL]),
                draft_recorder=lambda: injector.get(BotPublishService),
            )

        def device_sync(ctx: DeviceContext) -> DeviceSync:
            if ctx.provider == TECLAW_DEVICE_PROVIDER:
                return teclaw_device_sync(ctx)
            return baas_device_sync(ctx)

        return CommunityDeviceSyncDispatcher(device_sync_factory=device_sync)


def _lookup_bot(injector: Injector, bot_id: str) -> dict[str, Any]:
    """Read the ``ac_bots`` row backing ``bot_id`` (empty dict when unavailable).

    Neither a missing row nor a repository error is fatal *here*. ``dispatch``
    is a construction seam: its callers only expect
    ``DeviceSyncUnavailableError``, so letting a DB error escape would turn a
    recoverable delivery failure into an unhandled 500. Degrading to ``{}``
    keeps the failure where every other teclaw failure lands — the service's
    own ``{"success": False, ...}`` result, since a delivery that really needs
    the row cannot resolve a binding or compose without it.
    """
    from agentclaw.community.core.repository.protocols.bot import BotRepository

    try:
        return injector.get(BotRepository).get_by_id(bot_id) or {}
    except Exception as e:
        logger.warning(
            "[CommunityDeviceSyncModule] bot row lookup failed bot=%s: %s", bot_id, e
        )
        return {}
