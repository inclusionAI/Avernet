"""Local dispatcher merge, fan-out, and shutdown behavior."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DisabledBotManagerSync,
    DispatcherPolicy,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class RecordingService:
    def __init__(self, *, gated: bool = False) -> None:
        self.calls: list[SyncKey] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not gated:
            self.release.set()

    async def sync_once(self, key: SyncKey) -> SyncOutcome:
        self.calls.append(key)
        self.entered.set()
        await self.release.wait()
        return SyncOutcome.CONFIRMED


async def spin_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(0)


def submit(dispatcher, bot_pk=7, *, tenant="a", reason=SyncReason.COLLABORATOR_CHANGED):
    return dispatcher.request_sync(
        bot_pk=bot_pk,
        env="dev",
        avernet_tenant=tenant,
        reason=reason,
    )


def test_admission_is_memory_only_and_bounded_before_attach():
    service = Mock()
    reader = Mock()
    dispatcher = BotManagerSyncDispatcher(
        service,
        reader,
        policy=DispatcherPolicy(max_keys=1),
    )
    assert submit(dispatcher, 1) is SubmissionResult.QUEUED
    assert submit(dispatcher, 1) is SubmissionResult.COALESCED
    assert submit(dispatcher, 2) is SubmissionResult.REJECTED
    service.assert_not_called()
    reader.assert_not_called()
    assert dispatcher.stats.entries == 1


def test_disabled_posture_holds_no_resources():
    disabled = DisabledBotManagerSync()
    assert submit(disabled) is SubmissionResult.DISABLED
    assert (
        disabled.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
        is SubmissionResult.DISABLED
    )


@pytest.mark.asyncio
async def test_same_key_notifications_coalesce_into_one_round():
    service = RecordingService()
    dispatcher = BotManagerSyncDispatcher(service, Mock())
    assert submit(dispatcher) is SubmissionResult.QUEUED
    assert (
        submit(dispatcher, reason=SyncReason.OWNER_HANDOFF)
        is SubmissionResult.COALESCED
    )
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert service.calls == [SyncKey("a", "dev", 7)]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_notification_during_run_schedules_one_dirty_rerun():
    service = RecordingService(gated=True)
    dispatcher = BotManagerSyncDispatcher(service, Mock())
    await dispatcher.attach()
    try:
        assert submit(dispatcher) is SubmissionResult.QUEUED
        await service.entered.wait()
        assert submit(dispatcher) is SubmissionResult.COALESCED
        assert (
            submit(dispatcher, reason=SyncReason.OWNER_HANDOFF)
            is SubmissionResult.COALESCED
        )
        service.release.set()
        assert await dispatcher.drain(5)
        assert service.calls == [SyncKey("a", "dev", 7)] * 2
    finally:
        service.release.set()
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_owner_handoff_is_an_ordinary_roster_refresh():
    class SyncOnlyService:
        def __init__(self) -> None:
            self.calls = []

        async def sync_once(self, key):
            self.calls.append(key)
            return SyncOutcome.CONFIRMED

    service = SyncOnlyService()
    dispatcher = BotManagerSyncDispatcher(service, Mock())
    await dispatcher.attach()
    try:
        assert (
            submit(dispatcher, reason=SyncReason.OWNER_HANDOFF)
            is SubmissionResult.QUEUED
        )
        assert await dispatcher.drain(5)
        assert service.calls == [SyncKey("a", "dev", 7)]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_same_pk_in_two_tenants_is_two_keys_and_context_isolated():
    seen: list[tuple[SyncKey, str]] = []

    class ContextService:
        async def sync_once(self, key):
            seen.append((key, get_current_avernet_tenant()))
            return SyncOutcome.CONFIRMED

    dispatcher = BotManagerSyncDispatcher(ContextService(), Mock())
    await dispatcher.attach()
    try:
        assert submit(dispatcher, tenant="a") is SubmissionResult.QUEUED
        assert submit(dispatcher, tenant="b") is SubmissionResult.QUEUED
        assert await dispatcher.drain(5)
        assert set(seen) == {
            (SyncKey("a", "dev", 7), "a"),
            (SyncKey("b", "dev", 7), "b"),
        }
    finally:
        await dispatcher.stop()


def test_env_mismatch_rejected_without_service_or_reader_call():
    service = Mock()
    reader = Mock()
    dispatcher = BotManagerSyncDispatcher(service, reader, env="dev")
    assert (
        dispatcher.request_sync(
            bot_pk=1,
            env="pre",
            avernet_tenant="a",
            reason=SyncReason.REPAIR,
        )
        is SubmissionResult.REJECTED
    )
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "pre", ("space",), ()))
        is SubmissionResult.REJECTED
    )
    service.assert_not_called()
    reader.assert_not_called()


@pytest.mark.asyncio
async def test_space_pages_are_read_under_explicit_tenant_scope():
    pages = [[SyncKey("tenant-x", "dev", 1), SyncKey("tenant-x", "dev", 2)], []]
    seen_scopes: list[str] = []

    def page_space_keys(change, *, after_pk, limit):
        seen_scopes.append(get_current_avernet_tenant())
        return tuple(pages.pop(0))

    reader = Mock(page_space_keys=Mock(side_effect=page_space_keys))
    service = RecordingService()
    dispatcher = BotManagerSyncDispatcher(
        service,
        reader,
        policy=DispatcherPolicy(page_size=2),
    )
    await dispatcher.attach()
    try:
        assert (
            dispatcher.request_space_sync(
                SpaceChange("tenant-x", "dev", ("space",), ())
            )
            is SubmissionResult.QUEUED
        )
        assert await dispatcher.drain(5)
        assert seen_scopes == ["tenant-x", "tenant-x"]
        assert {key.bot_pk for key in service.calls} == {1, 2}
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_close_rejects_all_new_admission_including_existing_key():
    service = RecordingService(gated=True)
    dispatcher = BotManagerSyncDispatcher(service, Mock())
    await dispatcher.attach()
    try:
        assert submit(dispatcher) is SubmissionResult.QUEUED
        await service.entered.wait()
        dispatcher.close_admission()
        assert submit(dispatcher) is SubmissionResult.REJECTED
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
            is SubmissionResult.REJECTED
        )
        service.release.set()
        assert await dispatcher.drain(5)
        assert service.calls == [SyncKey("a", "dev", 7)]
    finally:
        service.release.set()
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_submissions_from_threads_wake_workers_without_loss():
    service = RecordingService()
    dispatcher = BotManagerSyncDispatcher(
        service,
        Mock(),
        policy=DispatcherPolicy(max_keys=32),
    )
    await dispatcher.attach()
    try:
        results: list[SubmissionResult] = []
        threads = [
            threading.Thread(
                target=lambda pk=pk: results.append(submit(dispatcher, pk))
            )
            for pk in range(1, 17)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results.count(SubmissionResult.QUEUED) == 16
        assert await dispatcher.drain(5)
        assert {key.bot_pk for key in service.calls} == set(range(1, 17))
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_stop_cancels_workers_and_is_idempotent_when_detached():
    service = RecordingService(gated=True)
    dispatcher = BotManagerSyncDispatcher(service, Mock())
    await dispatcher.attach()
    assert submit(dispatcher) is SubmissionResult.QUEUED
    await service.entered.wait()
    await dispatcher.stop()
    assert dispatcher.stats.closed
    assert not dispatcher.stats.attached
    assert submit(dispatcher) is SubmissionResult.REJECTED
    await dispatcher.stop()
