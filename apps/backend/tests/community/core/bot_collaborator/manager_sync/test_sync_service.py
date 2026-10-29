"""Best-effort sync service behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
)
from agentclaw.community.core.bot_collaborator.manager_sync.retry import SyncRunPolicy
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    DeliveryFailure,
    ManagerSetChange,
    SnapshotUnavailable,
    SyncFacts,
    SyncKey,
    SyncOutcome,
)
from agentclaw.community.plugin_api.bcs_managers import BcsManagersError
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin


def facts(*users: str, key: SyncKey | None = None) -> SyncFacts:
    key = key or SyncKey("tenant", "dev", 7)
    return SyncFacts(
        key,
        "bot",
        "entity",
        "owner",
        tuple(CollaboratorFact(user, "admin", True) for user in users),
    )


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_service(*, reader=None, outcomes=(), policy=None, clock=None, wait=None):
    reader = reader or Mock(read_facts=Mock(return_value=facts("member")))
    bcs = RecordingBcsManagersPlugin()
    bcs.outcomes.extend(outcomes)
    diagnostics = SyncDiagnostics()
    service = BotManagerSyncService(
        reader,
        bcs,
        policy=policy,
        clock=clock,
        jitter=lambda value: value,
        wait=wait,
        diagnostics=diagnostics,
    )
    return service, reader, bcs, diagnostics


@pytest.mark.asyncio
async def test_confirmed_empty_and_drift_outcomes_use_actual_plugin_calls():
    key = SyncKey("tenant", "dev", 7)
    reader = Mock()
    reader.read_facts.side_effect = [
        facts("member", key=key),
        facts(key=key),
        facts("member", key=key),
    ]
    service, _, bcs, diagnostics = build_service(
        reader=reader,
        outcomes=[
            ManagerSetChange(1, 0, 1, "one"),
            ManagerSetChange(0, 1, 0, "empty"),
            ManagerSetChange(0, 0, 0, "drift"),
        ],
    )

    assert await service.sync_once(key) is SyncOutcome.CONFIRMED
    assert await service.sync_once(key) is SyncOutcome.ACKNOWLEDGED_EMPTY
    assert await service.sync_once(key) is SyncOutcome.DRIFT
    assert [call.manager_user_ids for call in bcs.calls] == [
        ("member",),
        (),
        ("member",),
    ]
    assert diagnostics.counted[("send_confirmed", "confirmed")] == 1
    assert diagnostics.counted[("send_acknowledged_empty", "acknowledged_empty")] == 1
    assert diagnostics.counted[("send_drift", "drift")] == 1


@pytest.mark.asyncio
async def test_unknown_retries_with_fresh_facts_and_one_two_second_backoff():
    key = SyncKey("tenant", "dev", 7)
    reader = Mock()
    reader.read_facts.side_effect = [
        facts("old", key=key),
        facts("new", key=key),
        facts("newest", key=key),
    ]
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    service, _, bcs, diagnostics = build_service(
        reader=reader,
        outcomes=[
            BcsManagersError(DeliveryFailure.UNKNOWN, "timeout"),
            BcsManagersError(DeliveryFailure.NOT_SENT, "connect"),
            ManagerSetChange(1, 0, 1, "ok"),
        ],
        wait=wait,
    )

    assert await service.sync_once(key) is SyncOutcome.CONFIRMED
    assert waits == [1.0, 2.0]
    assert reader.read_facts.call_count == 3
    assert [call.manager_user_ids for call in bcs.calls] == [
        ("old",),
        ("new",),
        ("newest",),
    ]
    assert diagnostics.counted[("send_retryable", "failed")] == 2


@pytest.mark.asyncio
async def test_plain_timeout_is_retryable_and_re_reads_facts():
    key = SyncKey("tenant", "dev", 7)
    reader = Mock()
    reader.read_facts.side_effect = [facts("before", key=key), facts("after", key=key)]

    class TimeoutOnce(RecordingBcsManagersPlugin):
        async def replace_managers(self, snapshot, *, timeout_seconds):
            self.calls.append(snapshot)
            if len(self.calls) == 1:
                raise TimeoutError("transport detail must not escape")
            return ManagerSetChange(1, 0, 1, "ok")

    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    plugin = TimeoutOnce()
    service = BotManagerSyncService(
        reader,
        plugin,
        jitter=lambda value: value,
        wait=wait,
    )
    assert await service.sync_once(key) is SyncOutcome.CONFIRMED
    assert waits == [1.0]
    assert [call.manager_user_ids for call in plugin.calls] == [
        ("before",),
        ("after",),
    ]


@pytest.mark.asyncio
async def test_retry_exhaustion_does_not_block_a_later_sync():
    key = facts("member").key
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    errors = [BcsManagersError(DeliveryFailure.UNKNOWN, "timeout") for _ in range(3)]
    service, reader, bcs, diagnostics = build_service(outcomes=errors, wait=wait)

    assert await service.sync_once(key) is SyncOutcome.FAILED
    assert len(bcs.calls) == 3
    assert waits == [1.0, 2.0]
    assert diagnostics.counted[("retries_exhausted", "failed")] == 1

    assert await service.sync_once(key) is SyncOutcome.CONFIRMED
    assert len(bcs.calls) == 4
    assert reader.read_facts.call_count == 4


@pytest.mark.asyncio
async def test_rejected_is_terminal_but_not_permanent():
    key = facts("member").key
    service, _, bcs, _ = build_service(
        outcomes=[BcsManagersError(DeliveryFailure.REJECTED, "http_409")]
    )
    assert await service.sync_once(key) is SyncOutcome.FAILED
    assert len(bcs.calls) == 1
    assert await service.sync_once(key) is SyncOutcome.CONFIRMED
    assert len(bcs.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reader",
    [
        Mock(read_facts=Mock(side_effect=SnapshotUnavailable("not_found"))),
        Mock(read_facts=Mock(side_effect=RuntimeError("db failed"))),
    ],
)
async def test_read_failure_never_sends(reader):
    service, _, bcs, _ = build_service(reader=reader)
    assert await service.sync_once(facts().key) is SyncOutcome.FAILED
    assert bcs.calls == []


@pytest.mark.asyncio
async def test_reader_cannot_substitute_another_tenant_key():
    requested = SyncKey("tenant-a", "dev", 7)
    returned = facts("member", key=SyncKey("tenant-b", "dev", 7))
    reader = Mock(read_facts=Mock(return_value=returned))
    service, _, bcs, _ = build_service(reader=reader)
    assert await service.sync_once(requested) is SyncOutcome.FAILED
    assert bcs.calls == []


@pytest.mark.asyncio
async def test_oversized_projection_is_refused_without_send():
    oversized = facts(*(f"user-{index}" for index in range(1_001)))
    reader = Mock(read_facts=Mock(return_value=oversized))
    service, _, bcs, _ = build_service(reader=reader)
    assert await service.sync_once(oversized.key) is SyncOutcome.FAILED
    assert bcs.calls == []


@pytest.mark.asyncio
async def test_late_synchronous_read_result_is_discarded():
    clock = Clock()
    current = facts("member")

    def read_facts(key):
        clock.advance(16)
        return current

    reader = Mock(read_facts=Mock(side_effect=read_facts))
    service, _, bcs, diagnostics = build_service(reader=reader, clock=clock)
    assert await service.sync_once(current.key) is SyncOutcome.FAILED
    assert bcs.calls == []
    assert diagnostics.counted[("read_late", "failed")] == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_and_future_sync_is_allowed():
    key = facts("member").key
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingPlugin(RecordingBcsManagersPlugin):
        async def replace_managers(self, snapshot, *, timeout_seconds):
            entered.set()
            await release.wait()
            return await super().replace_managers(
                snapshot, timeout_seconds=timeout_seconds
            )

    reader = Mock(read_facts=Mock(return_value=facts("member")))
    plugin = BlockingPlugin()
    service = BotManagerSyncService(reader, plugin)
    task = asyncio.create_task(service.sync_once(key))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    release.set()
    assert await service.sync_once(key) is SyncOutcome.CONFIRMED


def test_policy_rejects_non_positive_limits():
    with pytest.raises(ValueError):
        SyncRunPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        SyncRunPolicy(budget_seconds=0)
