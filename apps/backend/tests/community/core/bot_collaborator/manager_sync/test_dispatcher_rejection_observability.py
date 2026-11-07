"""Finite diagnostics for admission loss and Space truncation."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import Counter
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
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
)


def submit(dispatcher, bot_pk=1, *, env="dev"):
    return dispatcher.request_sync(
        bot_pk=bot_pk,
        env=env,
        avernet_tenant="a",
        reason=SyncReason.COLLABORATOR_CHANGED,
    )


class GatedService:
    def __init__(self) -> None:
        self.calls: list[SyncKey] = []
        self.release = asyncio.Event()

    async def sync_once(self, key):
        self.calls.append(key)
        await self.release.wait()
        return SyncOutcome.CONFIRMED


class CoordinatedCounter(Counter):
    """Force unlocked read-modify-write callers to observe the same value."""

    def __init__(self) -> None:
        super().__init__()
        self._gate = threading.Lock()
        self._active = 0
        self._release = threading.Event()

    def __getitem__(self, key):
        value = super().__getitem__(key)
        with self._gate:
            self._active += 1
            if self._active == 2:
                self._release.set()
        self._release.wait(0.05)
        with self._gate:
            self._active -= 1
        return value


def test_diagnostics_counter_updates_are_atomic_and_snapshot_is_detached():
    diagnostics = SyncDiagnostics(logger=Mock())
    diagnostics._counted = CoordinatedCounter()
    key = SyncKey("a", "dev", 1)

    threads = [
        threading.Thread(
            target=diagnostics.record,
            args=("submit_rejected",),
            kwargs={"key": key, "outcome": "closed"},
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
        assert not thread.is_alive()

    snapshot = diagnostics.counted
    assert snapshot[("submit_rejected", "closed")] == 2
    snapshot[("submit_rejected", "closed")] = 99
    assert diagnostics.counted[("submit_rejected", "closed")] == 2


def test_normal_admission_records_finite_reason(caplog):
    caplog.set_level(logging.INFO)
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(Mock(), Mock(), diagnostics=diagnostics)

    assert submit(dispatcher, 1) is SubmissionResult.QUEUED
    assert (
        dispatcher.request_sync(
            bot_pk=1,
            env="dev",
            avernet_tenant="a",
            reason=SyncReason.OWNER_HANDOFF,
        )
        is SubmissionResult.COALESCED
    )
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
        is SubmissionResult.QUEUED
    )

    assert diagnostics.counted[("submit_admitted", "queued")] == 2
    assert diagnostics.counted[("submit_admitted", "coalesced")] == 1
    messages = [
        record.message
        for record in caplog.records
        if "submit_admitted" in record.message
    ]
    assert any("reason=collaborator_changed" in message for message in messages)
    assert any("reason=owner_handoff" in message for message in messages)
    assert any("reason=space_membership" in message for message in messages)


def test_closed_rejection_precedes_environment_mismatch():
    diagnostics = SyncDiagnostics(logger=Mock())
    dispatcher = BotManagerSyncDispatcher(
        Mock(), Mock(), env="dev", diagnostics=diagnostics
    )
    dispatcher.close_admission()

    assert submit(dispatcher, env="pre") is SubmissionResult.REJECTED
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "pre", ("space",), ()))
        is SubmissionResult.REJECTED
    )

    assert diagnostics.counted[("submit_rejected", "closed")] == 2
    assert ("submit_rejected", "env_mismatch") not in diagnostics.counted


def test_queue_full_closed_and_env_rejections_are_finite(caplog):
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        Mock(),
        Mock(),
        policy=DispatcherPolicy(max_keys=1),
        env="dev",
        diagnostics=diagnostics,
    )
    assert submit(dispatcher, 1) is SubmissionResult.QUEUED
    assert submit(dispatcher, 2) is SubmissionResult.REJECTED
    assert submit(dispatcher, 3, env="pre") is SubmissionResult.REJECTED
    dispatcher.close_admission()
    assert submit(dispatcher, 1) is SubmissionResult.REJECTED

    assert diagnostics.counted[("submit_rejected", "queue_full")] == 1
    assert diagnostics.counted[("submit_rejected", "env_mismatch")] == 1
    assert diagnostics.counted[("submit_rejected", "closed")] == 1
    messages = [record.message for record in caplog.records]
    assert sum("[MANAGER-SYNC] submit_rejected" in message for message in messages) == 3


@pytest.mark.asyncio
async def test_exact_full_page_with_more_candidates_records_truncation(caplog):
    pages = [
        (SyncKey("a", "dev", 1), SyncKey("a", "dev", 2)),
        (SyncKey("a", "dev", 3),),
    ]
    reader = Mock(page_space_keys=Mock(side_effect=pages))
    service = GatedService()
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        service,
        reader,
        policy=DispatcherPolicy(worker_count=1, max_keys=3, page_size=2),
        diagnostics=diagnostics,
    )
    await dispatcher.attach()
    try:
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
            is SubmissionResult.QUEUED
        )
        while dispatcher.stats.space_hints:
            await asyncio.sleep(0)
        assert dispatcher.stats.rejected_space_keys == 1
        assert diagnostics.counted[("space_expansion_truncated", "queue_full")] == 1
        assert any(
            "space_expansion_truncated" in record.message for record in caplog.records
        )
        service.release.set()
        assert await dispatcher.drain(5)
        assert {key.bot_pk for key in service.calls} == {1, 2}
    finally:
        service.release.set()
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_exact_full_final_page_uses_bounded_lookahead_without_false_truncation():
    reader = Mock(
        page_space_keys=Mock(
            side_effect=[
                (SyncKey("a", "dev", 1), SyncKey("a", "dev", 2)),
                (),
            ]
        )
    )

    class ImmediateService:
        async def sync_once(self, key):
            return SyncOutcome.CONFIRMED

    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        ImmediateService(),
        reader,
        policy=DispatcherPolicy(worker_count=1, max_keys=10, page_size=2),
        diagnostics=diagnostics,
    )
    await dispatcher.attach()
    try:
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
            is SubmissionResult.QUEUED
        )
        assert await dispatcher.drain(5)
        assert reader.page_space_keys.call_count == 2
        assert ("space_expansion_truncated", "queue_full") not in diagnostics.counted
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_reader_exception_text_is_not_logged(caplog):
    reader = Mock()
    reader.page_space_keys.side_effect = RuntimeError("secret SQL and payload")

    class ImmediateService:
        async def sync_once(self, key):
            return SyncOutcome.CONFIRMED

    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        ImmediateService(), reader, diagnostics=diagnostics
    )
    await dispatcher.attach()
    try:
        dispatcher.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
        assert await dispatcher.drain(5)
        assert diagnostics.counted[("space_expansion_failed", "failed")] == 1
        combined = "\n".join(record.message for record in caplog.records)
        assert "space_expansion_failed" in combined
        assert "secret SQL and payload" not in combined
    finally:
        await dispatcher.stop()
