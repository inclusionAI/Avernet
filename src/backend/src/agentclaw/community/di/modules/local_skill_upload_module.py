"""Composition binding for Local Skill package uploads."""

from __future__ import annotations

from injector import Injector, inject, provider, singleton

from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skills_pool.edit_guard import SkillsPoolEditGuard


class LocalSkillUploadBindings:
    """Keep package validation composition out of the aggregate Skill module."""

    @singleton
    @provider
    @inject
    def local_skill_upload_service(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_service_factory: SkillServiceFactory,
        audit_log_repo: BotCollabLogRepositoryProtocol,
        edit_guard: SkillsPoolEditGuard,
        injector: Injector,
        runtime_reconciler: BotRuntimeProjectorProtocol,
    ) -> LocalSkillUploadServiceProtocol:
        return LocalSkillUploadService(
            skill_repo,
            bot_repo,
            collaborator_service,
            skill_service_factory,
            audit_log_repo,
            edit_guard,
            lambda: injector.get(DeviceContextResolver),
            runtime_reconciler,
            SkillPackageValidator(SkillParser()),
        )
