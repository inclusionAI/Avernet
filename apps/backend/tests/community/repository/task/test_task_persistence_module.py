"""DI wiring: TaskPersistenceModule binds the 5 protocols to their impls as
singletons, on top of a TestingDatabaseModule-provided DatabasePlugin."""
from injector import Injector, Module, provider, singleton

from agentclaw.community.di.modules.testing_database_module import TestingDatabaseModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactApiServiceProtocol,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_api_service import (
    TaskArtifactApiService,
)
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
    NullBcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    BcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.session_files.bcs_session_files_client import (
    BcsSessionFilesClient,
)
from agentclaw.community.di.modules.infrastructure.community.task_runner_integration import (
    BcsTokenProviderImpl,
)


class _ConfiguredBcsModule(Module):
    @singleton
    @provider
    def bcs_token(self) -> BcsTokenProvider:
        return BcsTokenProviderImpl(
            base_url="http://bcs.example",
            token="hmac-token",
            secret="hmac-secret",
        )

    @singleton
    @provider
    def bcs_bot_tokens(self) -> BcsBotTokenProvider:
        return NullBcsBotTokenProvider()


def _injector() -> Injector:
    return Injector([TestingDatabaseModule(), TaskPersistenceModule()])


def test_each_protocol_resolves_to_its_impl():
    inj = _injector()
    assert isinstance(inj.get(TaskInfoRepositoryProtocol), TaskInfoRepository)
    assert inj.get(TaskNodeRepositoryProtocol) is not None
    assert inj.get(TaskNodeRunInfoRepositoryProtocol) is not None
    assert inj.get(TaskNodeRelationRepositoryProtocol) is not None
    assert inj.get(TaskCallbackRepositoryProtocol) is not None


def test_bindings_are_singletons():
    inj = _injector()
    assert inj.get(TaskInfoRepositoryProtocol) is inj.get(TaskInfoRepositoryProtocol)
    assert inj.get(TaskArtifactApiServiceProtocol) is inj.get(
        TaskArtifactApiServiceProtocol
    )


def test_artifact_service_wires_configured_bcs_session_files():
    inj = Injector(
        [TestingDatabaseModule(), TaskPersistenceModule(), _ConfiguredBcsModule()]
    )

    service = inj.get(TaskArtifactApiServiceProtocol)

    assert isinstance(service, TaskArtifactApiService)
    assert isinstance(service._session_files, BcsSessionFilesClient)
    assert service._bcs_bot_tokens is inj.get(BcsBotTokenProvider)
