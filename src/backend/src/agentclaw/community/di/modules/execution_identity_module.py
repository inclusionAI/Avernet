"""Composition-root bindings for execution identity."""

from injector import Binder, Injector, Module, provider, singleton

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.execution_identity_service import (
    ExecutionIdentityServiceProtocol,
)
from agentclaw.community.core.execution_identity.protocols import (
    RuntimePassportTokenUpdaterProtocol,
)
from agentclaw.community.core.execution_identity.service import ExecutionIdentityService
from agentclaw.community.core.repository.implementations.identity.execution_identity import (
    ExecutionIdentityRepository,
)
from agentclaw.community.core.repository.protocols.identity import (
    ExecutionIdentityRepositoryProtocol,
)


class ExecutionIdentityModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            ExecutionIdentityRepositoryProtocol,
            to=ExecutionIdentityRepository,
            scope=singleton,
        )
        binder.bind(ExecutionIdentityService, to=ExecutionIdentityService, scope=singleton)

    @singleton
    @provider
    def service_protocol(
        self, service: ExecutionIdentityService
    ) -> ExecutionIdentityServiceProtocol:
        return service

    @singleton
    @provider
    def runtime_updater(
        self, injector: Injector
    ) -> RuntimePassportTokenUpdaterProtocol:
        # Resolve through the public BotService port at the composition root;
        # the execution-identity core does not depend on its concrete class.
        return injector.get(BotServiceProtocol)


__all__ = ["ExecutionIdentityModule"]
