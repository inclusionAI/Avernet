"""Queued projection recovery rediscovering outstanding state after a crash."""

import pytest
from injector import InstanceProvider

from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_projection import (
    CapabilityProjectionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    CapabilityProjectionReconcileProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.capability_projection_recovery import (
    CapabilityProjectionRecovery,
)
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


@pytest.mark.asyncio
async def test_capability_recovery_discovers_committed_work_and_uses_latest_revision(test_injector):
    await test_injector.get(DatabasePlugin).bootstrap()
    repository = test_injector.get(CapabilityProjectionRepositoryProtocol)
    bots = test_injector.get(BotRepository)
    sent_scopes = []

    class RecordingRuntime:
        async def reconcile(self, *, bot_id, entity_id):
            state = repository.prepare(bot_id=bot_id, entity_id=entity_id)
            assert state is not None
            sent_scopes.append((state.tenant, state.version, state.scope.claimed_mcp))
            delivered = RuntimeProjectionResult.converged()
            assert repository.complete(state, delivered)
            return delivered

    test_injector.binder.bind(
        CapabilityProjectionReconcileProtocol, to=InstanceProvider(RecordingRuntime()),
    )
    recovery = test_injector.get(CapabilityProjectionRecovery)
    await recovery.bootstrap()
    addresses = {"bot_id": "with-work", "entity_id": "bot-entity"}
    with avernet_tenant_scope("tenant-with-work"):
        bots.insert({
            **addresses, "owner_id": "owner", "creator_id": "owner", "entity_type": "staff",
        })
        committed = repository.require(
            **addresses, scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"first"})),
        )

    # The writer has quit with no queue enqueue. Two scans join the same task.
    recovery.reconcile_once()
    with avernet_tenant_scope("tenant-with-work"):
        newer = repository.require(
            **addresses, scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"second"})),
        )
    recovery.reconcile_once()

    worker = test_injector.get(TaskWorker)
    assert await worker.run_once() == 1
    assert sent_scopes == [
        ("tenant-with-work", committed.version + 1, frozenset({"first", "second"})),
    ]
    with avernet_tenant_scope("tenant-with-work"):
        after = repository.read(**addresses)
        assert after is not None and after.version == newer.version
        assert after.result.status is RuntimeProjectionStatus.CONVERGED
    recovery.reconcile_once()
    assert await worker.run_once() == 0
