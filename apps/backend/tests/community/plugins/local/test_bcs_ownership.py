"""Recording fake models initialized fixtures, never registration."""

from collections import deque
from dataclasses import replace

import pytest

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure, SyncKey
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipSnapshot,
)
from agentclaw.community.plugin_api.bcs_ownership import (
    BcsOwnershipError,
    BcsOwnershipPlugin,
)
from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Flavor, Mode
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin


@pytest.fixture
def snapshot():
    return OwnershipSnapshot(SyncKey("tenant-a", "dev", 7), "bot:stable", "B")


@pytest.mark.asyncio
async def test_first_unscripted_call_confirms_initialized_fixture_idempotently(
    snapshot,
):
    plugin = RecordingBcsOwnershipPlugin()
    assert plugin.owners == {} and plugin.calls == []
    assert isinstance(plugin.outcomes, deque)
    result = await plugin.replace_ownership(snapshot, timeout_seconds=1)
    assert result == OwnershipChange("bot:stable", "B", None, False, "recorded-1")
    assert plugin.calls == [snapshot]
    assert plugin.owners == {snapshot.key: "B"}
    assert await plugin.replace_ownership(snapshot, timeout_seconds=1) == replace(
        result, request_id="recorded-2"
    )


@pytest.mark.asyncio
async def test_subsequent_swaps_report_real_previous_owner(snapshot):
    plugin = RecordingBcsOwnershipPlugin()
    plugin.owners[snapshot.key] = "A"
    first = await plugin.replace_ownership(snapshot, timeout_seconds=1)
    second = await plugin.replace_ownership(
        replace(snapshot, owner_user_id="C"), timeout_seconds=1
    )
    assert first == OwnershipChange("bot:stable", "B", "A", True, "recorded-1")
    assert second == OwnershipChange("bot:stable", "C", "B", True, "recorded-2")
    assert plugin.owners[snapshot.key] == "C"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_key",
    [
        SyncKey("tenant-b", "dev", 7),
        SyncKey("tenant-a", "prod", 7),
        SyncKey("tenant-a", "dev", 8),
    ],
)
async def test_state_is_isolated_by_full_sync_key(snapshot, other_key):
    plugin = RecordingBcsOwnershipPlugin()
    other = replace(snapshot, key=other_key, owner_user_id="X")
    await plugin.replace_ownership(snapshot, timeout_seconds=1)
    await plugin.replace_ownership(other, timeout_seconds=1)
    changed = await plugin.replace_ownership(
        replace(snapshot, owner_user_id="C"), timeout_seconds=1
    )
    noop = await plugin.replace_ownership(other, timeout_seconds=1)
    assert changed.previous_owner_user_id == "B"
    assert noop.owner_user_id == "X" and not noop.changed
    assert plugin.owners == {snapshot.key: "C", other_key: "X"}


@pytest.mark.asyncio
async def test_scripted_results_and_errors_are_consumed_recorded_without_mutating_state(
    snapshot,
):
    plugin = RecordingBcsOwnershipPlugin()
    missing = OwnershipChange("bot:stable", None, None, False, "missing")
    uninitialized = BcsOwnershipError(DeliveryFailure.REJECTED, "http_409")
    changed = OwnershipChange("other:bot", "X", "Y", True, "scripted")
    plugin.outcomes.extend([missing, uninitialized, changed])
    assert await plugin.replace_ownership(snapshot, timeout_seconds=1) is missing
    with pytest.raises(BcsOwnershipError) as caught:
        await plugin.replace_ownership(snapshot, timeout_seconds=1)
    assert caught.value is uninitialized
    assert await plugin.replace_ownership(snapshot, timeout_seconds=1) is changed
    assert plugin.calls == [snapshot] * 3
    assert not plugin.outcomes and plugin.owners == {}
    result = await plugin.replace_ownership(snapshot, timeout_seconds=1)
    assert not result.changed and result.request_id == "recorded-4"


@pytest.mark.asyncio
async def test_fake_state_is_per_instance_and_close_is_idempotent(snapshot):
    one, two = RecordingBcsOwnershipPlugin(), RecordingBcsOwnershipPlugin()
    await one.replace_ownership(snapshot, timeout_seconds=1)
    assert two.owners == {} and two.calls == [] and not two.outcomes
    await one.aclose()
    await one.aclose()
    assert one.owners == {snapshot.key: "B"}


def test_registered_as_local_fake_ownership_plugin():
    (entry,) = [
        entry for entry in IMPL_REGISTRY if entry.cls is RecordingBcsOwnershipPlugin
    ]
    assert entry.protocol is BcsOwnershipPlugin
    assert entry.mode is Mode.LOCAL and entry.flavor is Flavor.FAKE
