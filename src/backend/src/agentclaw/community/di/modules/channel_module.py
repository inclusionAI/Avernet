"""ChannelModule — production singletons for the channel module.

Bindings:

- ``ChannelRepository`` — a single unified ORM implementation that
  runs on whichever ``DatabasePlugin`` is bound (ZDAS in prod, SQLite
  in local/test via ``TestingDatabaseModule``). No per-mode override.
- ``ChannelService`` — mode-agnostic ``@singleton`` self-binding;
  ``@inject`` resolves the repo + cross-module
  ``DeviceFilesystemDispatcher`` / ``BotService`` via the injector.

``DeviceAccessor`` (the underlying singleton ``DeviceFilesystemDispatcher``
needs) is bound by :class:`SkillCenterModule` —
:class:`DeviceFilesystemDispatcher` itself lives there as well, so
the channel service simply pulls it in via ``@inject``.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.core.channel.services.bcs_binding_client import (
    BcsChannelBindingClientProtocol,
    HttpBcsChannelBindingClient,
)
from agentclaw.community.core.channel.services.channel_service import ChannelService
from agentclaw.community.core.channel.services.engine_overrides_reader import (
    ChannelEngineOverridesReader,
)
from agentclaw.community.core.repository.protocols.chat import ChannelRepository
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.core.repository.implementations.chat.channel import ChannelRepository as UnifiedChannelRepository


logger = get_logger()


class ChannelModule(Module):
    """Production bindings for the channel module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(ChannelService, to=ChannelService, scope=singleton)
        # Unified ORM repo (one body, ZDAS + SQLite). @inject ctor takes
        # the bound DatabasePlugin; prod vs test differ only by which
        # DatabasePlugin is bound (ZdasDB / SqliteDB).
        binder.bind(
            ChannelRepository, to=UnifiedChannelRepository, scope=singleton
        )

    @singleton
    @provider
    @inject
    def _channel_service_protocol(self, svc: ChannelService) -> ChannelServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _channel_engine_overrides_reader(
        self, channel_repo: ChannelRepository
    ) -> ChannelEngineOverridesReader:
        """Stage-scoped channel→engine_overrides reader. Consumed by the
        config-compose collector (draft filter) and the publish flow
        (verify/online filters); a singleton over the same ``ChannelRepository``."""
        return ChannelEngineOverridesReader(channel_repo=channel_repo)

    @singleton
    @provider
    def _bcs_channel_binding_client(
        self, config: cfg.BcsBindingConfig
    ) -> BcsChannelBindingClientProtocol:
        """BCS bindings orchestration client for ``bcn_gateway`` channels."""
        return HttpBcsChannelBindingClient(
            base_url=config.base_url,
            service_token=config.service_token,
            timeout=config.timeout_seconds,
        )
