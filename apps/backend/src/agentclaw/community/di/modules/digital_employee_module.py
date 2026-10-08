"""Default employee service wiring; enterprise overrides only the platform/config."""
from injector import Binder, Module, Injector, singleton, provider

from agentclaw.community.core.digital_employee.contracts import (
    DigitalEmployeeArtifactStorage, DigitalEmployeeRepositoryProtocol, DigitalEmployeeServiceProtocol, DigitalEmployeeSettings,
    DigitalEmployeePublicationProtocol, DigitalEmployeeCatalogProtocol,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.core.digital_employee.packages import DigitalEmployeePackageReader
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import LocalSkillUploadServiceProtocol
from agentclaw.community.core.skill_center.skill_service_factory_protocol import SkillServiceFactoryProtocol
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.core.digital_employee.catalog import DigitalEmployeeCatalogService
from agentclaw.community.core.digital_employee.service import DigitalEmployeeService
from agentclaw.community.core.digital_employee.publication import DigitalEmployeePublicationService
from agentclaw.community.core.repository.implementations.identity.digital_employee import DigitalEmployeeRepository
from agentclaw.community.plugin_api.digital_employee import DigitalEmployeePlatformPlugin
from agentclaw.community.plugins.community.digital_employee import UnavailableDigitalEmployeePlatform


class DigitalEmployeeModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(DigitalEmployeeCatalogProtocol, to=DigitalEmployeeCatalogService, scope=singleton)
        binder.bind(DigitalEmployeeSettings, to=DigitalEmployeeSettings(), scope=singleton)
        binder.bind(DigitalEmployeeRepositoryProtocol, to=DigitalEmployeeRepository, scope=singleton)
        binder.bind(DigitalEmployeeServiceProtocol, to=DigitalEmployeeService, scope=singleton)
        binder.bind(DigitalEmployeePublicationProtocol, to=DigitalEmployeePublicationService, scope=singleton)
        binder.bind(DigitalEmployeePlatformPlugin, to=UnavailableDigitalEmployeePlatform, scope=singleton)

    @singleton
    @provider
    def artifact_storage(self, config: cfg.ObjectStorageConfig) -> DigitalEmployeeArtifactStorage:
        return DigitalEmployeeArtifactStorage(bucket_name=config.bucket_name)

    @singleton
    @provider
    def scan_packages(self, injector: Injector, storage: DigitalEmployeeArtifactStorage) -> DigitalEmployeePackageReader:
        return DigitalEmployeePackageReader(
            injector.get(LocalSkillUploadServiceProtocol), injector.get(SkillServiceFactoryProtocol),
            SkillPackageValidator(SkillParser()), injector.get(ObjectStoragePlugin), storage,
        )
