"""ExpertChatModule — production singletons for the expert_chat module.

Bindings:

- ``ExpertChatRepository`` — a single unified ORM implementation that
  runs on whichever ``DatabasePlugin`` is bound (ZDAS in prod, SQLite
  in local/test via ``TestingDatabaseModule``). No per-mode override.
- ``ExpertChatService`` — mode-agnostic ``@singleton`` self-binding;
  ``@inject`` on its constructor resolves the repo + cross-module
  ``BotRepository`` / ``DeviceService`` via the injector.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.expert_chat_instance_service import (
    ExpertChatInstanceServiceProtocol,
)
from agentclaw.community.core.repository.protocols.chat import ExpertChatInstanceRepository
from agentclaw.community.core.repository.protocols.chat import ExpertChatRepository
from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import (
    ExpertChatInstanceService,
)
from agentclaw.community.core.expert_chat.services.expert_chat_service import ExpertChatService
from agentclaw.community.log import get_logger
from agentclaw.community.core.repository.implementations.chat.expert_chat import ExpertChatRepository as UnifiedExpertChatRepository
from agentclaw.community.core.repository.implementations.chat.expert_chat_instance import ExpertChatInstanceRepository as UnifiedExpertChatInstanceRepository


logger = get_logger()


class ExpertChatModule(Module):
    """Production bindings for the expert_chat module."""

    def configure(self, binder: Binder) -> None:
        binder.bind(ExpertChatService, to=ExpertChatService, scope=singleton)
        # Unified ORM repo (one body, ZDAS + SQLite). @inject ctor takes
        # the bound DatabasePlugin; prod vs test differ only by which
        # DatabasePlugin is bound (ZdasDB / SqliteDB).
        binder.bind(
            ExpertChatRepository, to=UnifiedExpertChatRepository, scope=singleton
        )

        # ExpertChatInstanceService — independent per-caller container
        # lifecycle (specs/2026-07-13-caller-instance). Stands alone;
        # ExpertChatService is intentionally not modified here. @inject
        # ctor resolves the instance repo + BaasService +
        # BotPublishRepositoryProtocol + BotRepository +
        # DeviceBindingRepository + DeviceContextResolver via the
        # injector (all bound by sibling modules).
        binder.bind(
            ExpertChatInstanceService,
            to=ExpertChatInstanceService,
            scope=singleton,
        )
        # Unified ORM impl for the ac_expert_chat_instance ledger (one
        # body, ZDAS + SQLite), same pattern as the session repo above.
        binder.bind(
            ExpertChatInstanceRepository,
            to=UnifiedExpertChatInstanceRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def _expert_chat_service_protocol(self, svc: ExpertChatService) -> ExpertChatServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _expert_chat_instance_service_protocol(
        self, svc: ExpertChatInstanceService
    ) -> ExpertChatInstanceServiceProtocol:
        return svc
