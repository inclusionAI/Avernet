"""Feature-off composition root for the owner-handoff journal and worker."""

from injector import Binder, Module, provider, singleton

from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    OwnerHandoffPolicy,
    OwnerHandoffRepositoryProtocol,
    OwnerHandoffServiceProtocol,
)
from agentclaw.community.core.bot_management.owner_handoff.service import (
    OwnerHandoffService,
)
from agentclaw.community.core.bot_management.owner_handoff.tasks import (
    OwnerHandoffLifecycle,
)
from agentclaw.community.core.repository.implementations.bot.owner_handoff import (
    OwnerHandoffRepository,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.di.config import TaskQueueWorkerConfig
from agentclaw.community.di.modules.config_module import read_user_config


def parse_handoff_policy(block, queue_enabled):
    if not isinstance(block, dict) or set(block) - {"enabled", "recovery_enabled"}:
        raise ValueError("Invalid owner_handoff config")
    if any(type(value) is not bool for value in block.values()):
        raise ValueError("owner_handoff flags must be booleans")
    enabled, recovery = (
        block.get("enabled", False),
        block.get("recovery_enabled", False),
    )
    if enabled and not recovery or recovery and not queue_enabled:
        raise ValueError("owner_handoff requires recovery and TaskQueue")
    return OwnerHandoffPolicy(
        enabled=enabled, recovery_enabled=recovery, administrators=super_admin()
    )


class OwnerHandoffModule(Module):
    def configure(self, binder: Binder):
        binder.bind(
            OwnerHandoffRepositoryProtocol, to=OwnerHandoffRepository, scope=singleton
        )
        binder.bind(
            OwnerHandoffServiceProtocol, to=OwnerHandoffService, scope=singleton
        )

    @singleton
    @provider
    def policy(self, queue: TaskQueueWorkerConfig) -> OwnerHandoffPolicy:
        return parse_handoff_policy(
            read_user_config().get("owner_handoff", {}), queue.enabled
        )

    @singleton
    @provider
    def lifecycle(
        self,
        registry: HandlerRegistry,
        service: OwnerHandoffServiceProtocol,
        policy: OwnerHandoffPolicy,
    ) -> OwnerHandoffLifecycle:
        return OwnerHandoffLifecycle(registry=registry, service=service, policy=policy)
