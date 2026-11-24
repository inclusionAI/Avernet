"""Internal L5 execution controls and replaceable feedback planner bindings."""

from injector import AssistedBuilder, Binder, Module, inject, provider, singleton

from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver import (
    SwarmDriver,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    ConservativeInteractionPlanner,
    SwarmDriverFactory,
    SwarmExecutionControl,
    SwarmInteractionPlanner,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.service import (
    SwarmExecutionOperations,
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
        binder.bind(
            SwarmExecutionOperations, to=SwarmExecutionOperations, scope=singleton
        )

    @provider
    def execution_control(
        self, service: SwarmExecutionOperations
    ) -> SwarmExecutionControl:
        return service
