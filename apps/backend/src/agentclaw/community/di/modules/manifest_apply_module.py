"""Composition root for the Manifest Apply run service and its Protocol."""

from typing import Callable

from injector import Module, inject, provider, singleton

from agentclaw.community.api.apply_run_service import ApplyRunServiceProtocol
from agentclaw.community.api.bot_config_manifest_service import BotConfigManifestServiceProtocol
from agentclaw.community.core.apply_engine.compiler import StateCompilers
from agentclaw.community.core.apply_engine.completion import StateCompletionHandlers
from agentclaw.community.core.apply_engine.delivery import DeliveryStrategyFactory
from agentclaw.community.core.apply_engine.run.service import ApplyRunService
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import CapabilityProjectionReconcileProtocol
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin, MCPServerPermissionPlugin


class ManifestApplyModule(Module):
    """Assemble one Apply instance and expose the same instance via its Protocol.

    The queue is resolved lazily to avoid a container import cycle. Binding the
    Protocol directly to the class would bypass this provider and construct a
    second instance without the lazy dependency.
    """

    @singleton
    @provider
    @inject
    def bot_config_manifest_apply_service(
        self,
        manifest_service: BotConfigManifestServiceProtocol,
        apply_repository: BotConfigManifestApplyRepositoryProtocol,
        lock_repository: BotConfigManifestApplyLockRepositoryProtocol,
        task_queue_provider: Callable[[], TaskQueueService],
        bot_repository: BotRepository,
        delivery_strategies: DeliveryStrategyFactory,
        compilers: StateCompilers,
        completion_handlers: StateCompletionHandlers,
        capability_projection: CapabilityProjectionReconcileProtocol,
        mcp_auth: MCPAuthPlugin,
    ) -> ApplyRunService:
        """Assemble Apply from its delivery, compiler, and completion bindings."""
        return ApplyRunService(
            manifest_service,
            apply_repository,
            lock_repository,
            task_queue_provider,
            bot_repository,
            delivery_strategies=delivery_strategies,
            compilers=compilers,
            completion_handlers=completion_handlers,
            capability_projection=capability_projection,
            server_permission_check=mcp_auth.check_server_permission if isinstance(mcp_auth, MCPServerPermissionPlugin) else None,
        )

    @singleton
    @provider
    @inject
    def bot_config_manifest_apply_service_protocol(
        self, service: ApplyRunService
    ) -> ApplyRunServiceProtocol:
        """The Protocol every adapter injects, delegated to the one instance."""
        return service
