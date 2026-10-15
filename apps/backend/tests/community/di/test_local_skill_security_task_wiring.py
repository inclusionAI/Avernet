"""The real composition root registers both independent security tasks."""

import asyncio
from unittest.mock import Mock

import pytest

from agentclaw.community.core.repository.implementations.skill_center.local_skill_security_observation import (
    LocalSkillSecurityObservationRepository,
)
from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.plugin_api.local_skill_security_provider import (
    LocalSkillSecurityProviderProtocol,
)
from agentclaw.community.core.skill_center.services.local_skill_security_tasks import (
    SCAN_TASK,
    SYNC_TASK,
    LocalSkillSecurityScanTaskHandler,
    LocalSkillSecuritySyncTaskHandler,
    LocalSkillSecurityTaskLifecycle,
)
from agentclaw.community.core.skill_center.services.local_skill_security_reconciler import (
    LocalSkillSecurityTaskReconciler,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete
from agentclaw.community.di.modules.local_skill_security_task_module import (
    LocalSkillSecurityTaskBindings,
    UnavailableLocalSkillSecurityProvider,
)
from agentclaw.community.di.local_skill_security_config import (
    LocalSkillSecurityObservationConfig,
)
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectStoragePlugin,
)
from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants


def test_local_skill_security_task_handlers_are_registered_in_composition_root(
    test_injector,
):
    repository = test_injector.get(LocalSkillSecurityObservationRepositoryProtocol)
    provider = test_injector.get(LocalSkillSecurityProviderProtocol)
    config = test_injector.get(LocalSkillSecurityObservationConfig)
    scan = test_injector.get(LocalSkillSecurityScanTaskHandler)
    sync = test_injector.get(LocalSkillSecuritySyncTaskHandler)
    lifecycle = test_injector.get(LocalSkillSecurityTaskLifecycle)
    reconciler = test_injector.get(LocalSkillSecurityTaskReconciler)
    registry = test_injector.get(HandlerRegistry)

    assert isinstance(repository, LocalSkillSecurityObservationRepository)
    assert isinstance(provider, UnavailableLocalSkillSecurityProvider)
    assert config.enabled is False
    assert isinstance(reconciler, LocalSkillSecurityTaskReconciler)
    asyncio.run(lifecycle.bootstrap())
    assert registry.get(SCAN_TASK) is scan
    assert registry.get(SYNC_TASK) is sync
    assert isinstance(scan.handle({"invalid": "old task"}), Complete)
    assert isinstance(sync.handle({"invalid": "old task"}), Complete)


def test_security_lifecycle_is_discovered_before_worker_starts(test_injector):
    participants = discover_lifecycle_participants(test_injector)
    assert any(isinstance(item, LocalSkillSecurityTaskLifecycle) for item in participants)
    assert any(isinstance(item, LocalSkillSecurityTaskReconciler) for item in participants)


def test_disabled_lifecycle_registers_handlers_with_mutable_only_storage(
    test_injector,
):
    storage = Mock(spec=ObjectStoragePlugin)
    assert isinstance(storage, ObjectStoragePlugin)
    assert not isinstance(storage, ImmutableObjectStorageCapability)
    test_injector.binder.bind(ObjectStoragePlugin, to=storage)

    lifecycle = test_injector.get(LocalSkillSecurityTaskLifecycle)
    registry = test_injector.get(HandlerRegistry)
    asyncio.run(lifecycle.bootstrap())

    assert isinstance(registry.get(SCAN_TASK).handle({"old": "task"}), Complete)
    assert isinstance(registry.get(SYNC_TASK).handle({"old": "task"}), Complete)
    assert storage.mock_calls == []
    with pytest.raises(ValueError, match="immutable snapshot storage"):
        LocalSkillSecurityTaskBindings().local_skill_security_scan_task_handler(
            repository=test_injector.get(
                LocalSkillSecurityObservationRepositoryProtocol
            ),
            object_storage=storage,
            security_provider=test_injector.get(LocalSkillSecurityProviderProtocol),
            security_config=LocalSkillSecurityObservationConfig(enabled=True),
        )
