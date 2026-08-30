"""Service-API aliases kept separate from the Skill Center composition root."""

from __future__ import annotations

from injector import inject, provider, singleton

from agentclaw.community.api.git_sync_service import GitSyncServiceProtocol
from agentclaw.community.api.runtime_layout_probe_service import RuntimeLayoutProbeServiceProtocol
from agentclaw.community.api.skill_auth_service import SkillAuthServiceProtocol
from agentclaw.community.api.skill_batch_sync_service import SkillBatchSyncServiceProtocol
from agentclaw.community.api.skill_center_sync_service import SkillCenterSyncServiceProtocol
from agentclaw.community.api.skill_center_gateway_service import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.api.skill_member_service import SkillMemberServiceProtocol
from agentclaw.community.api.skill_parameter_service_factory import SkillParameterServiceFactoryProtocol
from agentclaw.community.api.skill_propagation_service import SkillPropagationServiceProtocol
from agentclaw.community.api.skill_publish_service import SkillPublishServiceProtocol
from agentclaw.community.api.skill_scan_service import SkillScanServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_service_factory import SkillSetServiceFactoryProtocol
from agentclaw.community.core.skill_center.factories import (
    SkillParameterServiceFactory,
    SkillServiceFactory,
    SkillSetServiceFactory,
)
from agentclaw.community.core.skill_center.services.git_sync import GitSyncService
from agentclaw.community.core.skill_center.services.runtime_layout_probe import CurrentRuntimeLayoutProbeService
from agentclaw.community.core.skill_center.services.skill_auth_service import SkillAuthService
from agentclaw.community.core.skill_center.services.skill_batch_sync_service import SkillBatchSyncService
from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
from agentclaw.community.core.skill_center.services import (
    skill_center_gateway_service,
)
from agentclaw.community.core.skill_center.services.skill_member_service import SkillMemberService
from agentclaw.community.core.skill_center.services.skill_propagation_service import SkillPropagationService
from agentclaw.community.core.skill_center.services.skill_publish_service import SkillPublishService
from agentclaw.community.core.skill_center.services.skill_scan import SkillScanService


class SkillCenterProtocolBindings:
    """Keep adapter-facing Service API aliases out of the large DI module."""

    @singleton
    @provider
    @inject
    def _git_sync_service_protocol(self, svc: GitSyncService) -> GitSyncServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_auth_service_protocol(self, svc: SkillAuthService) -> SkillAuthServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_batch_sync_service_protocol(self, svc: SkillBatchSyncService) -> SkillBatchSyncServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_center_sync_service_protocol(self, svc: SkillCenterSyncService) -> SkillCenterSyncServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_center_gateway_service_protocol(
        self, svc: skill_center_gateway_service.SkillCenterGatewayService
    ) -> SkillCenterGatewayServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_member_service_protocol(self, svc: SkillMemberService) -> SkillMemberServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_parameter_service_factory_protocol(self, svc: SkillParameterServiceFactory) -> SkillParameterServiceFactoryProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_propagation_service_protocol(self, svc: SkillPropagationService) -> SkillPropagationServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_publish_service_protocol(self, svc: SkillPublishService) -> SkillPublishServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _runtime_layout_probe_service_protocol(self, svc: CurrentRuntimeLayoutProbeService) -> RuntimeLayoutProbeServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_scan_service_protocol(self, svc: SkillScanService) -> SkillScanServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_service_factory_protocol(self, svc: SkillServiceFactory) -> SkillServiceFactoryProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_set_service_factory_protocol(self, svc: SkillSetServiceFactory) -> SkillSetServiceFactoryProtocol:
        return svc
