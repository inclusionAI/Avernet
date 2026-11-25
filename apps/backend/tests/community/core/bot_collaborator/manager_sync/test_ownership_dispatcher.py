"""Explicit owner/manager work shares one bounded, per-key dispatcher."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
    SyncEvent,
    SyncRejection,
)
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DispatcherPolicy,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncKey,
    SyncOutcome,
    SyncReason,
    SyncWorkKind,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

KEY = SyncKey("t", "dev", 7)


class RecordingService:
    def __init__(
        self,
        kind: str,
        calls: list[tuple[str, SyncKey, str]],
        *,
        gated: bool = False,
        outcome: SyncOutcome = SyncOutcome.CONFIRMED,
        error: Exception | None = None,
    ) -> None:
        self.kind = kind
        self.calls = calls
        self.outcome = outcome
        self.error = error
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        if not gated:
            self.release.set()

    async def sync_once(self, key: SyncKey) -> SyncOutcome:
        self.calls.append((self.kind, key, get_current_avernet_tenant()))
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if self.error is not None:
            raise self.error
        return self.outcome


def submit(
    dispatcher, kind=SyncWorkKind.OWNERSHIP, key=KEY, *, reason=SyncReason.REPAIR
):
    request = (
        dispatcher.request_ownership_sync
        if kind is SyncWorkKind.OWNERSHIP
        else dispatcher.request_sync
    )
    return request(
        bot_pk=key.bot_pk,
        env=key.env,
        avernet_tenant=key.avernet_tenant,
        reason=reason,
    )


async def entered(service):
    await asyncio.wait_for(service.entered.wait(), timeout=5)


def kinds(calls):
    return [kind for kind, _, _ in calls]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_first", [False, True])
async def test_mixed_pending_runs_owner_then_one_manager_refresh(owner_first):
    calls = []
    owner = RecordingService("owner", calls, gated=True)
    managers = RecordingService("managers", calls)
    dispatcher = BotManagerSyncDispatcher(managers, Mock(), ownership_service=owner)
    first, second = (
        (SyncWorkKind.OWNERSHIP, SyncWorkKind.MANAGERS)
        if owner_first
        else (SyncWorkKind.MANAGERS, SyncWorkKind.OWNERSHIP)
    )
    assert submit(dispatcher, first) is SubmissionResult.QUEUED
    assert submit(dispatcher, second) is SubmissionResult.COALESCED
    assert submit(dispatcher, first) is SubmissionResult.COALESCED
    assert dispatcher.stats.entries == 1
    await dispatcher.attach()
    try:
        await entered(owner)
        assert kinds(calls) == ["owner"]
        owner.release.set()
        assert await dispatcher.drain(5)
        assert kinds(calls) == ["owner", "managers"]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_managers_only_owner_handoff_reason_does_not_call_owner():
    calls = []
    dispatcher = BotManagerSyncDispatcher(
        RecordingService("managers", calls),
        Mock(),
        ownership_service=RecordingService("owner", calls),
    )
    assert (
        submit(dispatcher, SyncWorkKind.MANAGERS, reason=SyncReason.OWNER_HANDOFF)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert kinds(calls) == ["managers"]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_owner_only_confirmation_calls_fresh_managers_service():
    calls = []
    owner = RecordingService("owner", calls, gated=True)
    managers = RecordingService("managers", calls, gated=True)
    dispatcher = BotManagerSyncDispatcher(managers, Mock(), ownership_service=owner)
    assert submit(dispatcher) is SubmissionResult.QUEUED
    await dispatcher.attach()
    try:
        await entered(owner)
        assert kinds(calls) == ["owner"]
        owner.release.set()
        await entered(managers)
        assert dispatcher.stats.entries == dispatcher.stats.running == 1
        assert kinds(calls) == ["owner", "managers"]
        managers.release.set()
        assert await dispatcher.drain(5)
        assert dispatcher.stats.entries == 0
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_managers", [False, True])
@pytest.mark.parametrize(
    "outcome",
    [
        SyncOutcome.FAILED,
        SyncOutcome.BOT_MISSING_NOOP,
        SyncOutcome.DRIFT,
        SyncOutcome.ACKNOWLEDGED_EMPTY,
    ],
)
async def test_unconfirmed_owner_only_runs_already_requested_managers(
    outcome, with_managers
):
    calls = []
    dispatcher = BotManagerSyncDispatcher(
        RecordingService("managers", calls),
        Mock(),
        ownership_service=RecordingService("owner", calls, outcome=outcome),
    )
    submit(dispatcher)
    if with_managers:
        submit(dispatcher, SyncWorkKind.MANAGERS)
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert kinds(calls) == ["owner"] + (["managers"] if with_managers else [])
        assert dispatcher.stats.entries == 0
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_unexpected_owner_exception_does_not_skip_requested_managers():
    calls = []
    dispatcher = BotManagerSyncDispatcher(
        RecordingService("managers", calls),
        Mock(),
        ownership_service=RecordingService(
            "owner", calls, error=RuntimeError("failed")
        ),
    )
    submit(dispatcher)
    submit(dispatcher, SyncWorkKind.MANAGERS)
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert kinds(calls) == ["owner", "managers"]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("during", ["owner", "managers"])
async def test_running_notifications_of_both_kinds_survive_finalization(during):
    calls = []
    owner = RecordingService("owner", calls, gated=during == "owner")
    managers = RecordingService("managers", calls, gated=during == "managers")
    dispatcher = BotManagerSyncDispatcher(managers, Mock(), ownership_service=owner)
    submit(dispatcher)
    await dispatcher.attach()
    try:
        await entered(owner if during == "owner" else managers)
        for _ in range(3):
            assert submit(dispatcher) is SubmissionResult.COALESCED
            assert (
                submit(dispatcher, SyncWorkKind.MANAGERS) is SubmissionResult.COALESCED
            )
        assert dispatcher.stats.entries == dispatcher.stats.running == 1
        owner.release.set()
        managers.release.set()
        assert await dispatcher.drain(5)
        assert kinds(calls) == ["owner", "managers", "owner", "managers"]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_same_pk_isolated_by_tenant_and_env_with_tenant_context():
    calls = []
    dispatcher = BotManagerSyncDispatcher(
        RecordingService("managers", calls),
        Mock(),
        ownership_service=RecordingService("owner", calls),
    )
    keys = [KEY, SyncKey("other", "dev", 7), SyncKey("t", "pre", 7)]
    for key in keys:
        assert submit(dispatcher, key=key) is SubmissionResult.QUEUED
    assert dispatcher.stats.entries == 3
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert set(calls) == {
            (kind, key, key.avernet_tenant)
            for key in keys
            for kind in ("owner", "managers")
        }
    finally:
        await dispatcher.stop()


def test_owner_admission_memory_only_and_kind_diagnostics():
    owner, managers, reader = Mock(), Mock(), Mock()
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        managers,
        reader,
        ownership_service=owner,
        env="dev",
        policy=DispatcherPolicy(max_keys=1),
        diagnostics=diagnostics,
    )
    assert submit(dispatcher) is SubmissionResult.QUEUED
    assert submit(dispatcher, SyncWorkKind.MANAGERS) is SubmissionResult.COALESCED
    assert submit(dispatcher) is SubmissionResult.COALESCED
    assert submit(dispatcher, key=SyncKey("t", "dev", 8)) is SubmissionResult.REJECTED
    assert submit(dispatcher, key=SyncKey("t", "pre", 7)) is SubmissionResult.REJECTED
    assert dispatcher.stats.entries == 1
    assert owner.mock_calls == managers.mock_calls == reader.mock_calls == []
    counts = diagnostics.counted_by_kind
    assert (
        counts[
            (SyncWorkKind.OWNERSHIP, SyncEvent.SUBMIT_ADMITTED, SubmissionResult.QUEUED)
        ]
        == 1
    )
    assert (
        counts[
            (
                SyncWorkKind.OWNERSHIP,
                SyncEvent.SUBMIT_ADMITTED,
                SubmissionResult.COALESCED,
            )
        ]
        == 1
    )
    assert (
        counts[
            (
                SyncWorkKind.MANAGERS,
                SyncEvent.SUBMIT_ADMITTED,
                SubmissionResult.COALESCED,
            )
        ]
        == 1
    )
    for rejection in (SyncRejection.QUEUE_FULL, SyncRejection.ENV_MISMATCH):
        assert (
            counts[(SyncWorkKind.OWNERSHIP, SyncEvent.SUBMIT_REJECTED, rejection)] == 1
        )


def test_disabled_owner_capability_records_diagnostic_without_admission():
    diagnostics = SyncDiagnostics()
    managers, reader = Mock(), Mock()
    dispatcher = BotManagerSyncDispatcher(managers, reader, diagnostics=diagnostics)
    assert submit(dispatcher) is SubmissionResult.DISABLED
    assert dispatcher.stats.entries == 0
    assert (
        diagnostics.counted_by_kind[
            (
                SyncWorkKind.OWNERSHIP,
                SyncEvent.SUBMIT_ADMITTED,
                SubmissionResult.DISABLED,
            )
        ]
        == 1
    )
    assert managers.mock_calls == reader.mock_calls == []


@pytest.mark.asyncio
async def test_default_four_workers_and_1024_capacity_include_running_and_space_hints():
    calls = []
    all_workers_entered = asyncio.Event()
    release = asyncio.Event()
    worker_tasks = set()
    worker_loops = set()

    class Owner:
        async def sync_once(self, key):
            calls.append(key)
            worker_tasks.add(asyncio.current_task())
            worker_loops.add(asyncio.get_running_loop())
            if len(calls) == 4:
                all_workers_entered.set()
            await release.wait()
            return SyncOutcome.FAILED

    dispatcher = BotManagerSyncDispatcher(Mock(), Mock(), ownership_service=Owner())
    assert DispatcherPolicy().page_size == 100
    for pk in range(1023):
        assert (
            submit(dispatcher, key=SyncKey("t", "dev", pk)) is SubmissionResult.QUEUED
        )
    assert (
        dispatcher.request_space_sync(SpaceChange("t", "dev", ("space",), ()))
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        await asyncio.wait_for(all_workers_entered.wait(), 5)
        assert len(calls) == len(worker_tasks) == 4
        assert worker_loops == {asyncio.get_running_loop()}
        assert dispatcher.stats.running == 4
        assert dispatcher.stats.entries == 1023
        assert dispatcher.stats.space_hints == 1
        assert (
            submit(dispatcher, key=SyncKey("t", "dev", 1024))
            is SubmissionResult.REJECTED
        )
        assert (
            submit(dispatcher, key=SyncKey("t", "dev", 0)) is SubmissionResult.COALESCED
        )
        assert (
            submit(dispatcher, SyncWorkKind.MANAGERS, SyncKey("t", "dev", 0))
            is SubmissionResult.COALESCED
        )
        assert (
            dispatcher.request_space_sync(SpaceChange("t", "dev", ("other",), ()))
            is SubmissionResult.REJECTED
        )
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_full_queue_owner_followup_uses_existing_slot_during_closed_drain():
    calls = []
    owner = RecordingService("owner", calls, gated=True)
    managers = RecordingService("managers", calls, gated=True)
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        managers,
        Mock(),
        ownership_service=owner,
        env="dev",
        diagnostics=diagnostics,
        policy=DispatcherPolicy(worker_count=1, max_keys=2),
    )
    submit(dispatcher)
    other = SyncKey("t", "dev", 8)
    submit(dispatcher, SyncWorkKind.MANAGERS, other)
    await dispatcher.attach()
    try:
        await entered(owner)
        assert dispatcher.stats.entries == 2
        assert (
            submit(dispatcher, key=SyncKey("t", "dev", 9)) is SubmissionResult.REJECTED
        )
        dispatcher.close_admission()
        for kind in SyncWorkKind:
            assert submit(dispatcher, kind) is SubmissionResult.REJECTED
            assert submit(dispatcher, kind, other) is SubmissionResult.REJECTED
        # Closed is the rejection even when the environment also mismatches.
        assert (
            submit(dispatcher, key=SyncKey("t", "pre", 7)) is SubmissionResult.REJECTED
        )
        assert (
            diagnostics.counted_by_kind[
                (
                    SyncWorkKind.OWNERSHIP,
                    SyncEvent.SUBMIT_REJECTED,
                    SyncRejection.CLOSED,
                )
            ]
            == 3
        )
        assert not await dispatcher.drain(0)
        owner.release.set()
        await entered(managers)
        assert dispatcher.stats.entries == 2
        assert calls[:2] == [("owner", KEY, "t"), ("managers", KEY, "t")]
        managers.release.set()
        assert await dispatcher.drain(5)
        assert calls == [
            ("owner", KEY, "t"),
            ("managers", KEY, "t"),
            ("managers", other, "t"),
        ]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("during", ["owner", "managers"])
async def test_shutdown_cancellation_propagates_without_followup_or_rerun(during):
    calls = []
    owner = RecordingService("owner", calls, gated=during == "owner")
    managers = RecordingService("managers", calls, gated=during == "managers")
    dispatcher = BotManagerSyncDispatcher(managers, Mock(), ownership_service=owner)
    submit(dispatcher)
    submit(dispatcher, SyncWorkKind.MANAGERS)
    await dispatcher.attach()
    try:
        running = owner if during == "owner" else managers
        await entered(running)
        submit(dispatcher)
        submit(dispatcher, SyncWorkKind.MANAGERS)
        await dispatcher.stop()
        assert running.cancelled.is_set()
        assert kinds(calls) == (
            ["owner"] if during == "owner" else ["owner", "managers"]
        )
        assert dispatcher.stats.entries == dispatcher.stats.running == 0
        assert not dispatcher.stats.attached
        assert submit(dispatcher) is SubmissionResult.REJECTED
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["outcome", "owner_exception", "manager_exception"])
async def test_failed_round_releases_key_and_later_notifications_are_admissible(
    failure,
):
    calls = []
    owner = RecordingService("owner", calls)
    managers = RecordingService("managers", calls)
    if failure == "outcome":
        owner.outcome = SyncOutcome.FAILED
    elif failure == "owner_exception":
        owner.error = RuntimeError("owner failed")
    else:
        managers.error = RuntimeError("managers failed")
    dispatcher = BotManagerSyncDispatcher(
        managers,
        Mock(),
        ownership_service=owner,
        policy=DispatcherPolicy(max_keys=1),
    )
    submit(dispatcher)
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert dispatcher.stats.entries == 0
        first_round = kinds(calls)
        assert first_round == (
            ["owner", "managers"] if failure == "manager_exception" else ["owner"]
        )
        owner.error = managers.error = None
        owner.outcome = SyncOutcome.CONFIRMED
        assert submit(dispatcher) is SubmissionResult.QUEUED
        assert submit(dispatcher, SyncWorkKind.MANAGERS) is SubmissionResult.COALESCED
        assert await dispatcher.drain(5)
        assert kinds(calls) == first_round + ["owner", "managers"]
    finally:
        await dispatcher.stop()
