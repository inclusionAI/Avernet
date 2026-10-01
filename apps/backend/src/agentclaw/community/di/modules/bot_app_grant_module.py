"""BotAppGrantModule — production bindings for application delegations.

Bindings:

- ``BotAppGrantRepositoryProtocol`` — binds to the unified
  ``BotAppGrantRepository``, which uses ``DatabasePlugin.orm_session()`` and so
  works unchanged on both SQLite and the corp store.
- ``BotAppGrantServiceProtocol`` — the Service API the public router depends on;
  the provider below wires the concrete ``BotAppGrantService`` behind it.
- ``UserAppGrantRepositoryProtocol`` / ``UserAppGrantServiceProtocol`` — the
  user-level delegation, same shape one level up: the record the public API
  admits an application on where no bot is addressed.

Tests construct the services directly with a fake repository; the bindings
exist so the public routes can use ``Injected(...)``.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_app_grant_service import (
    BotAppGrantServiceProtocol,
)
from agentclaw.community.api.user_app_grant_service import (
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.bot_app_grant.services import (
    BotAppGrantService,
    UserAppGrantService,
)
from agentclaw.community.core.repository.implementations.bot.app_grant import (
    BotAppGrantRepository,
)
from agentclaw.community.core.repository.implementations.bot.user_app_grant import (
    UserAppGrantRepository,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotAppGrantRepositoryProtocol,
    BotRepository,
    UserAppGrantRepositoryProtocol,
)


class BotAppGrantModule(Module):
    """Bindings for bot_app_grant."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BotAppGrantRepositoryProtocol,
            to=BotAppGrantRepository,
            scope=singleton,
        )
        binder.bind(
            UserAppGrantRepositoryProtocol,
            to=UserAppGrantRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def bot_app_grant_service(
        self,
        repository: BotAppGrantRepositoryProtocol,
        bots: BotRepository,
    ) -> BotAppGrantServiceProtocol:
        """Provide the grant service behind its Service API Protocol.

        Bound as the Protocol, not the class: the public router injects the
        contract, so the concrete service stays swappable and separately
        testable.
        """
        return BotAppGrantService(repository=repository, bots=bots)

    @singleton
    @provider
    @inject
    def user_app_grant_service(
        self,
        repository: UserAppGrantRepositoryProtocol,
    ) -> UserAppGrantServiceProtocol:
        """Provide the user-level delegation service behind its Protocol."""
        return UserAppGrantService(repository=repository)
