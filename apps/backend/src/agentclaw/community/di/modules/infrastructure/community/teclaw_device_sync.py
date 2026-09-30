"""Teclaw ``DeviceSync`` construction — the ``provider=teclaw`` DI component.

Owns one thing: how a resolved :class:`DeviceContext` becomes a
:class:`TeclawDeviceSyncService`, which delivers the bot's whole composed
``BotConfigArtifact`` to the running container. It knows nothing about routing
or about the other providers — ``CommunityDeviceSyncModule`` installs this
module and injects the factory it binds.

Every collaborator arrives by constructor injection, so this module resolves
nothing through the ``Injector`` at call time. ``ConfigComposer`` and
``BotPublishService`` are handed to the service as ``Callable[[], T]`` thunks
because that is the shape its constructor takes (it defers both until a
delivery actually happens); ``BotPublishService`` is bound as a thunk upstream
in ``BotManagementModule.bot_publish_service_factory``, which is also what
breaks the ``BotService`` construction cycle.
"""
from __future__ import annotations

from typing import Annotated, Any, Callable

from injector import Module, inject, provider, singleton

from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.devices.services.teclaw_device_sync import (
    TeclawDeviceSyncService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_GENERAL

logger = get_logger()


class TeclawDeviceSyncFactory:
    """Builds the per-bot teclaw ``DeviceSync`` for a resolved ``DeviceContext``.

    A callable rather than a bare function so it is an injectable type with a
    name the dispatcher module can depend on.
    """

    def __init__(
        self,
        *,
        baas_service: BaasService,
        bot_repo: BotRepository,
        http_client: HttpClient,
        composer_provider: Callable[[], ConfigComposer],
        draft_recorder: Callable[[], BotPublishService],
    ) -> None:
        self._baas_service = baas_service
        self._bot_repo = bot_repo
        self._http_client = http_client
        self._composer_provider = composer_provider
        self._draft_recorder = draft_recorder

    def __call__(self, ctx: DeviceContext) -> DeviceSync:
        # The bot row carries the identity fields the composer needs.
        # ``entity_id`` (compose scope) and ``owner_id`` (the identity the
        # binding was resolved under) are deliberately kept apart — see the
        # TeclawDeviceSyncService module docstring.
        bot = self._lookup_bot(ctx.bot_id)
        return TeclawDeviceSyncService(
            conn_info=ctx.conn_info,
            bot_id=ctx.bot_id,
            bot_name=bot.get("bot_name", ""),
            # The resolver already read this binding to build ``ctx``; passing
            # it through is what keeps ``_deliver`` off a second lookup for a
            # value it was handed. Declared non-optional on ``DeviceContext``.
            binding_id=ctx.binding_id,
            user_id=ctx.user_id,
            owner_id=bot.get("owner_id") or ctx.user_id,
            entity_id=bot.get("entity_id") or None,
            engine_type=bot.get("active_engine") or TECLAW_DEVICE_PROVIDER,
            entity_type=bot.get("entity_type") or "staff",
            composer_provider=self._composer_provider,
            baas_service=self._baas_service,
            http_client=self._http_client,
            draft_recorder=self._draft_recorder,
        )

    def _lookup_bot(self, bot_id: str) -> dict[str, Any]:
        """Read the ``ac_bots`` row backing ``bot_id`` (empty dict when unavailable).

        Neither a missing row nor a repository error is fatal *here*. This
        factory runs inside ``DeviceSyncDispatcher.dispatch``, a construction
        seam whose callers only expect ``DeviceSyncUnavailableError``, so
        letting a DB error escape would turn a recoverable delivery failure
        into an unhandled 500. Degrading to ``{}`` keeps the failure where
        every other teclaw failure lands — the service's own
        ``{"success": False, ...}`` result, since a delivery that really needs
        the row cannot resolve a binding or compose without it.
        """
        try:
            return self._bot_repo.get_by_id(bot_id) or {}
        except Exception as e:
            logger.warning(
                "[TeclawDeviceSyncFactory] bot row lookup failed bot=%s: %s", bot_id, e
            )
            return {}


class TeclawDeviceSyncModule(Module):
    """Bind :class:`TeclawDeviceSyncFactory`."""

    @singleton
    @provider
    @inject
    def teclaw_device_sync_factory(
        self,
        baas_service: BaasService,
        bot_repo: BotRepository,
        http_client: Annotated[HttpClient, QUALIFIER_GENERAL],
        composer: ConfigComposer,
        draft_recorder: Callable[[], BotPublishService],
    ) -> TeclawDeviceSyncFactory:
        return TeclawDeviceSyncFactory(
            baas_service=baas_service,
            bot_repo=bot_repo,
            http_client=http_client,
            composer_provider=lambda: composer,
            draft_recorder=draft_recorder,
        )
