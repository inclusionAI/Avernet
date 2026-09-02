"""UserAppGrantModule — production bindings for account-level user→app grants.

Bindings:

- ``UserAppGrantRepositoryProtocol`` — binds to the unified
  ``UserAppGrantRepository``, which uses ``DatabasePlugin.orm_session()`` and
  so works unchanged on both SQLite and the corp store.
- ``UserAppGrantServiceProtocol`` — the Service API the public router and the
  admission seam depend on; the provider below wires the concrete
  ``UserAppGrantService`` behind it.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.user_app_grant_service import (
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.repository.implementations.bot.user_app_grant import (
    UserAppGrantRepository,
)
from agentclaw.community.core.repository.protocols.bot import (
    UserAppGrantRepositoryProtocol,
)
from agentclaw.community.core.user_app_grant.services import UserAppGrantService


class UserAppGrantModule(Module):
    """Bindings for user_app_grant."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            UserAppGrantRepositoryProtocol,
            to=UserAppGrantRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def user_app_grant_service(
        self, repository: UserAppGrantRepositoryProtocol
    ) -> UserAppGrantServiceProtocol:
        """Provide the grant service behind its Service API Protocol."""
        return UserAppGrantService(repository=repository)
