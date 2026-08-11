"""DI bindings for the frontend-only entity user-list query service."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.user_list_service import UserListServiceProtocol
from agentclaw.community.core.user_list import (
    UserListRepositoryProtocol,
    UserListService,
)
from agentclaw.community.core.repository.implementations.identity.user_list import UserListRepository


class UserListModule(Module):
    """Bind the repository and the read-only application service."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            UserListRepositoryProtocol,
            to=UserListRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def user_list_service(
        self,
        repository: UserListRepositoryProtocol,
    ) -> UserListService:
        return UserListService(repository=repository)

    @singleton
    @provider
    @inject
    def user_list_service_protocol(
        self,
        service: UserListService,
    ) -> UserListServiceProtocol:
        return service
