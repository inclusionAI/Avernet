"""Compose local G3 acquisition with G1 access and independent SQL storage."""

from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.telemetry_access import (
    TelemetryAccess,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.persistence import (
    TelemetryRepository,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.service import (
    TelemetryService,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.service_api import (
    TelemetryServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from injector import Module, provider, singleton


class TelemetryModule(Module):
    @provider
    @singleton
    def telemetry_service(
        self,
        db: DatabasePlugin,
        repository: ContextCollabRepositoryProtocol,
        authorizer: ContextCollabAuthorizerProtocol,
    ) -> TelemetryServiceProtocol:
        return TelemetryService(
            TelemetryRepository(db), TelemetryAccess(repository, authorizer)
        )
