"""BotAppGrantModule — production bindings for owner-granted bot authorizations.

Bindings:

- ``BotAppGrantRepositoryProtocol`` — binds to the unified
  ``BotAppGrantRepository``, which uses ``DatabasePlugin.orm_session()`` and so
  works unchanged on both SQLite and the corp store.
- ``BotAppGrantServiceProtocol`` — the Service API the public router depends on;
  the provider below wires the concrete ``BotAppGrantService`` behind it.

Tests construct the service directly with a fake repository; the bindings exist
so the public routes can use ``Injected(...)``.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_app_grant_service import (
    BotAppGrantServiceProtocol,
)
from agentclaw.community.core.bot_app_grant.services import BotAppGrantService
from agentclaw.community.core.repository.implementations.bot.app_grant import (
    BotAppGrantRepository,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotAppGrantRepositoryProtocol,
)


class BotAppGrantModule(Module):
    """Bindings for bot_app_grant."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BotAppGrantRepositoryProtocol,
            to=BotAppGrantRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def bot_app_grant_service(
        self,
        repository: BotAppGrantRepositoryProtocol,
    ) -> BotAppGrantServiceProtocol:
        """Provide the grant service behind its Service API Protocol.

        Bound as the Protocol, not the class: the public router injects the
        contract, so the concrete service stays swappable and separately
        testable.
        """
        return BotAppGrantService(repository=repository)
