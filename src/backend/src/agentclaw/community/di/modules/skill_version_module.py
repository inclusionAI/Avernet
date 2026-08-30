"""Composition-root bindings for exact Version resolution/materialization."""

from typing import Annotated

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.skill_version_resolver import (
    SkillVersionResolver,
)
from agentclaw.community.core.skill_center.version_resolution_contract import (
    SkillVersionResolverProtocol,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializationRepositoryProtocol,
    SkillVersionMaterializerProtocol,
    SkillVersionScannerProtocol,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SdkSkillVersionScanner,
    SkillVersionMaterializer,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionStore,
)
from agentclaw.community.plugin_api.http_client import (
    HttpClient,
    QUALIFIER_GENERAL,
)
from agentclaw.community.plugin_api.skill_center_gateway import SkillCenterGateway


class SkillVersionModule(Module):
    """Wire the Version Resolution repository and Service API implementation."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            SkillVersionRepositoryProtocol,
            to=SkillVersionRepository,
            scope=singleton,
        )
        binder.bind(
            SkillVersionResolverProtocol,
            to=SkillVersionResolver,
            scope=singleton,
        )
        binder.bind(
            SkillVersionMaterializationRepositoryProtocol,
            to=SkillVersionRepository,
            scope=singleton,
        )
        binder.bind(
            SkillVersionScannerProtocol,
            to=SdkSkillVersionScanner,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def skill_version_materializer(
        self,
        versions: SkillVersionMaterializationRepositoryProtocol,
        gateway: SkillCenterGateway,
        http: Annotated[HttpClient, QUALIFIER_GENERAL],
        scanner: SkillVersionScannerProtocol,
        store: CanonicalCenterVersionStore,
    ) -> SkillVersionMaterializerProtocol:
        return SkillVersionMaterializer(
            versions=versions,
            gateway=gateway,
            http=http,
            validator=SkillPackageValidator(SkillParser()),
            scanner=scanner,
            store=store,
        )
