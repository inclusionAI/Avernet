"""Manual ownership repair admits scoped keys, never Owner snapshots."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock, call

import pytest

from agentclaw.community.api.bot_ownership_sync import BotOwnershipSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
)
from agentclaw.community.core.bot_collaborator.manager_sync.repair import RepairPage
from agentclaw.community.core.bot_management.ownership_sync.repair import (
    DisabledOwnershipSyncRepair,
    OwnershipSyncRepairService,
)
from agentclaw.community.core.bot_management.ownership_sync.service import (
    BotOwnershipSyncService,
)
from agentclaw.community.core.repository.implementations.bot.ownership_sync import (
    SqlOwnershipSyncReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts, OwnershipSnapshot
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.core.bot_collaborator.manager_sync.factories import seed_sync_facts
from tests.community.framework.fixtures import (
    app_with_testing_modules as app_with_testing_modules,
    world as world,
)
from tests.community.framework.world import World

KEY = SyncKey("tenant", "dev", 7)


@pytest.mark.parametrize("result", list(SubmissionResult))
def test_request_bot_passes_through_admission_without_reading(result):
    reader = Mock(spec=OwnershipSyncReadRepository)
    notifier = Mock(spec=BotOwnershipSyncProtocol)
    notifier.request_ownership_sync.return_value = result

    assert OwnershipSyncRepairService(reader, notifier).request_bot(KEY) is result
    notifier.request_ownership_sync.assert_called_once_with(
        bot_pk=7, env="dev", avernet_tenant="tenant", reason=SyncReason.REPAIR,
    )
    assert reader.mock_calls == []


def test_page_clamps_limit_and_preserves_exact_non_admitted_keys():
    keys = tuple(SyncKey("tenant", "dev", pk) for pk in range(11, 15))
    reader = Mock(spec=OwnershipSyncReadRepository)
    reader.page_keys.return_value = keys
    notifier = Mock(spec=BotOwnershipSyncProtocol)
    notifier.request_ownership_sync.side_effect = [
        SubmissionResult.QUEUED, SubmissionResult.COALESCED,
        SubmissionResult.DISABLED, SubmissionResult.REJECTED,
    ]

    page = OwnershipSyncRepairService(reader, notifier).request_page(
        avernet_tenant="tenant", env="dev", after_pk=10, limit=999,
    )

    assert type(page) is RepairPage
    assert page == RepairPage(14, 1, 1, 2, keys[2:])
    reader.page_keys.assert_called_once_with(
        avernet_tenant="tenant", env="dev", after_pk=10, limit=100,
    )
    reader.read_facts.assert_not_called()
    assert notifier.request_ownership_sync.call_args_list == [
        call(bot_pk=key.bot_pk, env=key.env, avernet_tenant=key.avernet_tenant,
             reason=SyncReason.REPAIR)
        for key in keys
    ]


@pytest.mark.parametrize("limit", [0, -1, -999])
def test_nonpositive_limit_returns_empty_page_without_sql_or_admission(limit):
    reader, notifier = Mock(), Mock()
    page = OwnershipSyncRepairService(reader, notifier).request_page(
        avernet_tenant="tenant", env="dev", after_pk=9, limit=limit,
    )
    assert page == RepairPage(9, 0, 0, 0, ())
    assert reader.mock_calls == notifier.mock_calls == []


def test_empty_page_keeps_cursor_without_admission():
    reader, notifier = Mock(), Mock()
    reader.page_keys.return_value = ()
    page = OwnershipSyncRepairService(reader, notifier).request_page(
        avernet_tenant="tenant", env="dev", after_pk=9, limit=10,
    )
    assert page == RepairPage(9, 0, 0, 0, ())
    reader.page_keys.assert_called_once_with(
        avernet_tenant="tenant", env="dev", after_pk=9, limit=10,
    )
    assert notifier.mock_calls == []


@pytest.mark.parametrize("wrong_key", [
    SyncKey("other", "dev", 8), SyncKey("tenant", "prod", 8),
])
def test_validates_entire_page_scope_before_any_admission(wrong_key):
    reader, notifier = Mock(), Mock()
    reader.page_keys.return_value = (KEY, wrong_key)
    with pytest.raises(ValueError, match="^ownership_repair_scope_mismatch$"):
        OwnershipSyncRepairService(reader, notifier).request_page(
            avernet_tenant="tenant", env="dev", after_pk=0, limit=10,
        )
    assert notifier.mock_calls == []


def test_reader_exception_propagates_without_admission():
    reader, notifier = Mock(), Mock()
    error = RuntimeError("database read failed")
    reader.page_keys.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        OwnershipSyncRepairService(reader, notifier).request_page(
            avernet_tenant="tenant", env="dev", after_pk=0, limit=10,
        )
    assert caught.value is error
    assert notifier.mock_calls == []


def test_disabled_repair_has_no_resource_dependencies_or_success_page():
    repair = DisabledOwnershipSyncRepair()
    assert isinstance(repair, OwnershipSyncRepairService)
    assert vars(repair) == {}
    assert repair.request_bot(KEY) is SubmissionResult.DISABLED
    for limit in (0, 100):
        with pytest.raises(RuntimeError, match="^ownership sync repair unavailable$"):
            repair.request_page(
                avernet_tenant="tenant", env="dev", after_pk=0, limit=limit,
            )


@pytest.mark.asyncio
async def test_repair_dispatcher_delivers_owner_changed_after_admission():
    reader = Mock(spec=OwnershipSyncReadRepository)
    reader.read_facts.return_value = OwnershipFacts(KEY, "bot", "stable", "old", bot_type="service")
    plugin = RecordingBcsOwnershipPlugin()
    owner_service = BotOwnershipSyncService(reader, plugin)
    managers = Mock(sync_once=AsyncMock(return_value=SyncOutcome.CONFIRMED))
    dispatcher = BotManagerSyncDispatcher(
        managers, Mock(), env="dev", ownership_service=owner_service,
    )
    repair = OwnershipSyncRepairService(reader, dispatcher)
    try:
        assert repair.request_bot(KEY) is SubmissionResult.QUEUED
        assert repair.request_bot(KEY) is SubmissionResult.COALESCED
        reader.read_facts.assert_not_called()
        assert plugin.calls == []
        reader.read_facts.return_value = replace(
            reader.read_facts.return_value, owner_id="new",
        )
        await dispatcher.attach()
        assert await dispatcher.drain(2)
        reader.read_facts.assert_called_once_with(KEY)
        assert len(plugin.calls) == 1
        assert plugin.calls[0].owner_user_id == "new"
        assert plugin.calls[0].bcs_bot_id == "bot:stable"
        managers.sync_once.assert_awaited_once_with(KEY)
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_repair_delivers_owner_committed_in_separate_sql_session(world: World):
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(
        db, tenant="tenant", env="dev", bot_pk=7, entity_id="stable", owner_id="B",
    )
    reader = SqlOwnershipSyncReadRepository(db)
    before = reader.read_facts(key)
    assert before == OwnershipFacts(key, "bot-stable", "stable", "B", bot_type="service")
    plugin = RecordingBcsOwnershipPlugin()
    managers = Mock(sync_once=AsyncMock(return_value=SyncOutcome.CONFIRMED))
    dispatcher = BotManagerSyncDispatcher(
        managers, Mock(), env="dev",
        ownership_service=BotOwnershipSyncService(reader, plugin),
    )
    repair = OwnershipSyncRepairService(reader, dispatcher)
    try:
        # Queue while B is committed, but do not allow any worker to read yet.
        assert repair.request_bot(key) is SubmissionResult.QUEUED
        assert not dispatcher.stats.attached
        assert plugin.calls == []

        # A separate real session commits C before the queued key is executed.
        with avernet_tenant_scope(key.avernet_tenant), db.orm_session() as session:
            assert session.query(BotModel).filter(
                BotModel.id == key.bot_pk, BotModel.env == key.env,
            ).update({"owner_id": "C"}) == 1

        await dispatcher.attach()
        assert await dispatcher.drain(2)
        assert before.owner_id == "B"
        assert plugin.calls == [OwnershipSnapshot(key, "bot-stable:stable", "C")]
        assert plugin.owners == {key: "C"}
        managers.sync_once.assert_awaited_once_with(key)
    finally:
        await dispatcher.stop()
