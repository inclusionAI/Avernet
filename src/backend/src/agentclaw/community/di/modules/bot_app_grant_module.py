"""BotAppGrantModule — production bindings for owner-granted bot authorizations.

Bindings:

- ``BotAppGrantRepositoryProtocol`` — binds to the unified
  ``BotAppGrantRepository``, which uses ``DatabasePlugin.orm_session()`` and so
  works unchanged on both SQLite and the corp store.
- ``BotAppGrantService`` — plain class; the provider wires it up.

Tests construct the service directly with a fake repository; the bindings exist
so the public routes can use ``Injected(...)``.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

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
    ) -> BotAppGrantService:
        """Provide the grant service over the bound repository."""
        return BotAppGrantService(repository=repository)
