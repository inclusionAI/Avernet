"""DI wiring: TaskPersistenceModule binds the 5 protocols to their impls as
singletons, on top of a TestingDatabaseModule-provided DatabasePlugin."""
from injector import Injector

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
