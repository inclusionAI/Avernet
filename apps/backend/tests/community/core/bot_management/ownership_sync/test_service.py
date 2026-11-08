"""Bounded ownership delivery uses fresh facts, never persisted retry state."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
)
from agentclaw.community.core.bot_collaborator.manager_sync.retry import SyncRunPolicy
from agentclaw.community.core.bot_management.ownership_sync.service import (
    BotOwnershipSyncService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    SnapshotUnavailable,
    SyncKey,
    SyncOutcome,
)
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipFacts,
)
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipError
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin

KEY = SyncKey("tenant", "dev", 7)
FACTS = OwnershipFacts(KEY, "bot", "stable", "B", bot_type="service")


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


def build_service(*, reader=None, plugin=None, clock=None, **kwargs):
    reader = reader if reader is not None else Mock(read_facts=Mock(return_value=FACTS))
    plugin = plugin if plugin is not None else RecordingBcsOwnershipPlugin()
    clock = clock if clock is not None else Clock()
    diagnostics = SyncDiagnostics()
    service = BotOwnershipSyncService(
        reader,
        plugin,
        clock=clock,
        wait=clock.wait,
        jitter=lambda value: value,
        diagnostics=diagnostics,
        **kwargs,
    )
    return service, reader, plugin, diagnostics, clock


@pytest.mark.asyncio
async def test_retry_rereads_current_owner():
    reader = Mock()
    reader.read_facts.side_effect = [FACTS, replace(FACTS, owner_id="C")]
    service, _, plugin, diagnostics, clock = build_service(reader=reader)
    plugin.outcomes.append(BcsOwnershipError(DeliveryFailure.UNKNOWN, "read_timeout"))
    assert await service.sync_once(KEY) is SyncOutcome.CONFIRMED
    assert [call.owner_user_id for call in plugin.calls] == ["B", "C"]
    assert reader.read_facts.call_args_list == [((KEY,),), ((KEY,),)]
    assert clock.waits == [1.0]
    assert (
        diagnostics.counted_by_kind[("ownership", "send_confirmed", "confirmed")] == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [DeliveryFailure.NOT_SENT, DeliveryFailure.UNKNOWN])
async def test_three_attempts_then_later_round_possible(kind):
    service, reader, plugin, diagnostics, clock = build_service()
    plugin.outcomes.extend(BcsOwnershipError(kind, "timeout") for _ in range(3))
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert len(plugin.calls) == reader.read_facts.call_count == 3
    assert clock.waits == [1.0, 2.0]
    assert diagnostics.counted[("retries_exhausted", "failed")] == 1
    assert await service.sync_once(KEY) is SyncOutcome.CONFIRMED
    assert len(plugin.calls) == 4


@pytest.mark.asyncio
async def test_rejected_is_terminal_not_permanent():
    service, _, plugin, diagnostics, clock = build_service()
    plugin.outcomes.append(BcsOwnershipError(DeliveryFailure.REJECTED, "http_403"))
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert len(plugin.calls) == 1
    assert clock.waits == []
    assert diagnostics.counted[("send_rejected", "failed")] == 1
    assert await service.sync_once(KEY) is SyncOutcome.CONFIRMED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [SnapshotUnavailable("missing"), RuntimeError("private db detail")]
)
async def test_read_failure_never_sends(error):
    service, reader, plugin, diagnostics, _ = build_service()
    reader.read_facts.side_effect = error
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert plugin.calls == []
    assert diagnostics.counted[("read_failed", "failed")] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["bot_id", "entity_id", "owner_id"])
async def test_invalid_identity_has_no_empty_default(field):
    reader = Mock(read_facts=Mock(return_value=replace(FACTS, **{field: ""})))
    service, _, plugin, diagnostics, _ = build_service(reader=reader)
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert plugin.calls == []
    assert diagnostics.counted[("projection_failed", "failed")] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        SyncKey("other", "dev", 7),
        SyncKey("tenant", "prod", 7),
        SyncKey("tenant", "dev", 8),
    ],
)
async def test_wrong_scope_never_sends(key):
    reader = Mock(read_facts=Mock(return_value=replace(FACTS, key=key)))
    service, _, plugin, _, _ = build_service(reader=reader)
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_late_read_discarded_and_read_runs_off_loop():
    clock = Clock()
    loop_thread = threading.get_ident()

    def read(key):
        assert threading.get_ident() != loop_thread
        clock.now += 15
        return FACTS

    reader = Mock(read_facts=Mock(side_effect=read))
    service, _, plugin, diagnostics, _ = build_service(reader=reader, clock=clock)
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert plugin.calls == []
    assert diagnostics.counted[("read_late", "failed")] == 1


@pytest.mark.asyncio
async def test_shared_fifteen_second_budget_clips_retry_delay():
    clock = Clock()
    timeouts = []

    class SlowPlugin(RecordingBcsOwnershipPlugin):
        async def replace_ownership(self, snapshot, *, timeout_seconds):
            self.calls.append(snapshot)
            timeouts.append(timeout_seconds)
            clock.now += 14.5
            raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "timeout")

    service, reader, plugin, diagnostics, _ = build_service(
        plugin=SlowPlugin(), clock=clock
    )
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert timeouts == [15.0]
    assert clock.waits == [0.5]
    assert len(plugin.calls) == reader.read_facts.call_count == 1
    assert diagnostics.counted[("deadline_expired", "failed")] == 1


@pytest.mark.asyncio
async def test_each_send_receives_only_remaining_budget_and_unexpected_errors_retry():
    clock = Clock()
    timeouts = []

    class BrokenPlugin(RecordingBcsOwnershipPlugin):
        async def replace_ownership(self, snapshot, *, timeout_seconds):
            self.calls.append(snapshot)
            timeouts.append(timeout_seconds)
            clock.now += 1
            raise RuntimeError("private transport detail")

    service, _, plugin, _, _ = build_service(plugin=BrokenPlugin(), clock=clock)
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert timeouts == [15.0, 13.0, 10.0]
    assert len(plugin.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bot,owner,changed,expected,event",
    [
        ("bot:stable", "B", True, SyncOutcome.CONFIRMED, "send_confirmed"),
        ("bot:stable", "B", False, SyncOutcome.CONFIRMED, "send_confirmed"),
        (
            "bot:stable",
            None,
            False,
            SyncOutcome.BOT_MISSING_NOOP,
            "send_bot_missing_noop",
        ),
        ("other", "B", False, SyncOutcome.DRIFT, "send_drift"),
        ("other", None, False, SyncOutcome.DRIFT, "send_drift"),
        ("bot:stable", "C", False, SyncOutcome.DRIFT, "send_drift"),
    ],
)
async def test_confirmation_requires_matching_bot_and_owner(
    bot, owner, changed, expected, event
):
    service, _, plugin, diagnostics, clock = build_service()
    plugin.outcomes.append(
        OwnershipChange(bot, owner, "A" if changed else None, changed, "req")
    )
    assert await service.sync_once(KEY) is expected
    assert len(plugin.calls) == 1
    assert clock.waits == []
    assert diagnostics.counted[(event, expected)] == 1
    assert sum(diagnostics.counted.values()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["read", "send", "backoff"])
async def test_cancellation_in_every_async_phase_records_once_and_propagates(phase):
    entered = asyncio.Event()
    release_read = threading.Event()
    loop = asyncio.get_running_loop()
    diagnostics = SyncDiagnostics()
    reader = Mock(read_facts=Mock(return_value=FACTS))

    def blocking_read(key):
        loop.call_soon_threadsafe(entered.set)
        release_read.wait()
        return FACTS

    class BlockingPlugin(RecordingBcsOwnershipPlugin):
        async def replace_ownership(self, snapshot, *, timeout_seconds):
            entered.set()
            await asyncio.Event().wait()

    async def blocking_wait(seconds):
        entered.set()
        await asyncio.Event().wait()

    plugin = BlockingPlugin() if phase == "send" else RecordingBcsOwnershipPlugin()
    if phase == "read":
        reader.read_facts.side_effect = blocking_read
    if phase == "backoff":
        plugin.outcomes.append(BcsOwnershipError(DeliveryFailure.UNKNOWN, "timeout"))
    service = BotOwnershipSyncService(
        reader, plugin, wait=blocking_wait, diagnostics=diagnostics
    )
    task = asyncio.create_task(service.sync_once(KEY))
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release_read.set()
    assert diagnostics.counted[("cancelled", "failed")] == 1
    assert diagnostics.counted_by_kind[("ownership", "cancelled", "failed")] == 1
    if phase == "read":
        assert plugin.calls == []


@pytest.mark.asyncio
async def test_policy_can_shorten_round():
    service, _, plugin, _, _ = build_service(
        policy=SyncRunPolicy(max_attempts=1, budget_seconds=2)
    )
    plugin.outcomes.append(BcsOwnershipError(DeliveryFailure.UNKNOWN, "timeout"))
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert len(plugin.calls) == 1


@pytest.mark.asyncio
async def test_expired_round_never_starts_a_read():
    clock = Mock(side_effect=[100.0, 115.0, 115.0])
    reader = Mock()
    plugin = RecordingBcsOwnershipPlugin()
    service = BotOwnershipSyncService(reader, plugin, clock=clock)
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    reader.read_facts.assert_not_called()
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_budget_exhausted_after_projection_never_sends():
    clock = Mock(side_effect=[100.0, 100.0, 100.0, 115.0, 115.0])
    reader = Mock(read_facts=Mock(return_value=FACTS))
    plugin = RecordingBcsOwnershipPlugin()
    service = BotOwnershipSyncService(reader, plugin, clock=clock)
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    reader.read_facts.assert_called_once_with(KEY)
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_exhausted_send_budget_does_not_start_backoff_or_second_read():
    clock = Clock()

    class DeadlinePlugin(RecordingBcsOwnershipPlugin):
        async def replace_ownership(self, snapshot, *, timeout_seconds):
            self.calls.append(snapshot)
            clock.now += timeout_seconds
            raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "timeout")

    service, reader, plugin, diagnostics, _ = build_service(
        plugin=DeadlinePlugin(),
        clock=clock,
    )
    assert await service.sync_once(KEY) is SyncOutcome.FAILED
    assert len(plugin.calls) == reader.read_facts.call_count == 1
    assert clock.waits == []
    assert diagnostics.counted[("deadline_expired", "failed")] == 1


@pytest.mark.asyncio
async def test_jitter_is_applied_to_each_retry_base_without_real_sleeps():
    clock = Clock()
    reader = Mock(read_facts=Mock(return_value=FACTS))
    plugin = RecordingBcsOwnershipPlugin()
    plugin.outcomes.extend(
        BcsOwnershipError(DeliveryFailure.UNKNOWN, "timeout") for _ in range(2)
    )
    jitter = Mock(side_effect=lambda base: base * 1.1)
    service = BotOwnershipSyncService(
        reader,
        plugin,
        clock=clock,
        jitter=jitter,
        wait=clock.wait,
    )
    assert await service.sync_once(KEY) is SyncOutcome.CONFIRMED
    assert jitter.call_args_list == [((1.0,),), ((2.0,),)]
    assert clock.waits == [1.1, 2.2]
