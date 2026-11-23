"""Composition root for bounded Swarm execution and replaceable feedback planner."""

from injector import AssistedBuilder, Binder, Module, inject, provider, singleton

from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver import (
    SwarmDriver,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    ConservativeInteractionPlanner,
    SwarmDriverFactory,
    SwarmExecutionServiceProtocol,
    SwarmInteractionPlanner,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_service import (
    SwarmExecutionService,
)


class InjectedSwarmDrivers:
    @inject
    def __init__(self, builder: AssistedBuilder[SwarmDriver]):
        self.builder = builder

    def build(self, collet_id: str) -> SwarmDriver:
        return self.builder.build(collet_id=collet_id)


class SwarmExecutionModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(SwarmDriverFactory, to=InjectedSwarmDrivers, scope=singleton)
        binder.bind(
            SwarmInteractionPlanner, to=ConservativeInteractionPlanner, scope=singleton
        )
        binder.bind(SwarmExecutionService, to=SwarmExecutionService, scope=singleton)

    @provider
    def service_api(
        self, service: SwarmExecutionService
    ) -> SwarmExecutionServiceProtocol:
        return service
