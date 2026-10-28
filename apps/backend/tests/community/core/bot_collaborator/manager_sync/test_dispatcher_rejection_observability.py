"""Rejection observability + latched-capacity regressions (P2 fix round).

Two review findings pinned on the unified admission boundary:

- **[P2-a] best-effort must never be silent.** Every REJECTED
  admission (queue_full / closed / env_mismatch) and every
  capacity-truncated Space expansion leaves exactly one
  ``SyncDiagnostics`` record plus one WARNING carrying the reason, the
  scope (submission vs space-fanout), the SyncKey dimensions and a
  repair-sweep compensation hint. No payload, no manager lists, and the
  truncated remainder is never enumerated. The caller still gets a
  plain REJECTED — no raise, no blocking.
- **[P2-b] a failed hold's latch is a capacity liability, not just a
  routing flag.** Latched keys whose entry is gone consume a
  ``max_keys`` slot: at the cap new admissions fail closed (and are
  recorded), while a latched key with spare capacity re-admits and
  still routes to the owner hold. The latch itself is never evicted —
  owner protection semantics are unchanged.

Same Event/fake-clock style as ``test_dispatcher.py``; that file sits at
the repo's 1,000-line CI ceiling, so this suite lives beside it and
reuses its doubles.
"""
from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from tests.community.core.bot_collaborator.manager_sync.test_dispatcher import (
    FakeClock,
    PagedReader,
    ScriptedService,
    spin_until,
)

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

#: The dispatcher WARNING prefix — filtering by the event word after it
#: ignores both the diagnostics sink's own aggregation line and the
#: pre-existing lifecycle warnings (defer_window_expired et al).
_DISPATCH_WARN_PREFIX = "[MANAGER-SYNC-DISPATCH] "


def _dispatch_warnings(caplog, kind: str) -> list[str]:
    prefix = _DISPATCH_WARN_PREFIX + kind
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and record.getMessage().startswith(prefix)
    ]


def _submit_bot(
    dispatcher: BotManagerSyncDispatcher,
    pk: int,
    *,
    env: str = "dev",
    reason: SyncReason = SyncReason.COLLABORATOR_CHANGED,
):
    return dispatcher.request_sync(
        bot_pk=pk, env=env, avernet_tenant="a", reason=reason
    )


# -- P2-a: a rejected submission is recorded, not just dropped -------------------


def test_queue_full_rejection_records_once_and_warns(caplog):
    """max_keys=1 + one live key: a second, different Bot is REJECTED
    with exactly one diagnostic and one WARNING (reason, scope, the
    SyncKey, the compensation hint)."""
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        ScriptedService(),
        Mock(),
        policy=DispatcherPolicy(max_keys=1),
        diagnostics=diagnostics,
    )
    assert _submit_bot(dispatcher, 1) is SubmissionResult.QUEUED
    with caplog.at_level(logging.WARNING):
        assert _submit_bot(dispatcher, 2) is SubmissionResult.REJECTED
    assert diagnostics.counted[("submit_rejected", "queue_full")] == 1
    warnings = _dispatch_warnings(caplog, "submit_rejected")
    assert len(warnings) == 1
    message = warnings[0]
    assert "reason=queue_full" in message
    assert "scope=submission" in message
    assert "tenant=a env=dev bot_pk=2" in message
    assert "repair sweep" in message


def test_closed_rejections_record_for_both_scopes(caplog):
    """A closed gate refuses both entry points observably: the bot
    submission logs scope=submission, the Space hint scope=space-fanout,
    both with reason=closed."""
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        ScriptedService(), Mock(), diagnostics=diagnostics
    )
    dispatcher.close_admission()
    with caplog.at_level(logging.WARNING):
        assert _submit_bot(dispatcher, 1) is SubmissionResult.REJECTED
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "dev", ("space:1",), ()))
            is SubmissionResult.REJECTED
        )
    assert diagnostics.counted[("submit_rejected", "closed")] == 2
    messages = _dispatch_warnings(caplog, "submit_rejected")
    assert len(messages) == 2
    assert sum("scope=submission" in m for m in messages) == 1
    assert sum("scope=space-fanout" in m for m in messages) == 1
    assert all("reason=closed" in m for m in messages)


def test_env_mismatch_rejections_record_for_both_scopes(caplog):
    """The env gate stays non-silent too: both refused scopes record
    reason=env_mismatch (the deployment env is on the line, never a
    payload), and the matching env is still admitted."""
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        ScriptedService(), Mock(), env="dev", diagnostics=diagnostics
    )
    with caplog.at_level(logging.WARNING):
        assert _submit_bot(dispatcher, 7, env="pre") is SubmissionResult.REJECTED
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "pre", ("space:1",), ()))
            is SubmissionResult.REJECTED
        )
    assert diagnostics.counted[("submit_rejected", "env_mismatch")] == 2
    messages = _dispatch_warnings(caplog, "submit_rejected")
    assert len(messages) == 2
    assert all("reason=env_mismatch" in m for m in messages)
    assert all("deployment_env=dev" in m for m in messages)
    assert sum("scope=submission" in m for m in messages) == 1
    assert sum("scope=space-fanout" in m for m in messages) == 1
    # Same deployment env still admits normally.
    assert _submit_bot(dispatcher, 7) is SubmissionResult.QUEUED


# -- P2-b: latched owner keys count toward capacity ------------------------------


@pytest.mark.asyncio
async def test_latched_key_without_entry_occupies_capacity_slot(caplog):
    """The reviewer's repro, bounded: max_keys=1, one failed handoff
    exhausts its defer window — entries drain to 0 but the sticky owner
    latch remains and still holds the one capacity slot, so a different
    Bot is refused (and recorded) instead of sneaking past the cap."""
    service = ScriptedService()
    service.hold_outcomes.extend([SyncOutcome.FAILED] * 8)
    clock = FakeClock()
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        service,
        Mock(),
        policy=DispatcherPolicy(worker_count=1, max_keys=1),
        clock=clock,
        diagnostics=diagnostics,
    )
    assert (
        _submit_bot(dispatcher, 7, reason=SyncReason.OWNER_HANDOFF)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        # The 1s/2s/4s/8s backoff inside the 15s window, then expiry.
        for attempt_no, step in enumerate((1.0, 2.0, 4.0, 8.0, None), start=1):
            await spin_until(
                lambda expected=attempt_no: len(service.hold_calls) == expected,
                what=f"hold attempt {attempt_no}",
            )
            if step is not None:
                clock.advance(step)
                dispatcher.wake()
        await spin_until(
            lambda: dispatcher.stats.entries == 0,
            what="failed-hold entry dropped after its window",
        )
        assert dispatcher.stats.latched_owner_keys == 1  # sticky, never evicted
        # entries == 0, yet the retained latch consumes max_keys' only slot.
        with caplog.at_level(logging.WARNING):
            assert _submit_bot(dispatcher, 8) is SubmissionResult.REJECTED
        assert diagnostics.counted[("submit_rejected", "queue_full")] == 1
        assert len(_dispatch_warnings(caplog, "submit_rejected")) == 1
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_latched_keys_fill_capacity_yet_route_holds(caplog):
    """max_keys=2: two failed holds expire their defer windows — the
    entries drain, both latches are retained (a same-key re-admission
    still routes to the hold while a slot is free), and the two
    latched keys fill the budget so a THIRD, different Bot fails
    closed with the full observability."""
    service = ScriptedService()
    service.hold_outcomes.extend([SyncOutcome.FAILED] * 24)
    clock = FakeClock()
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        service,
        Mock(),
        policy=DispatcherPolicy(worker_count=1, max_keys=2),
        clock=clock,
        diagnostics=diagnostics,
    )
    # Bot 1: one failed handoff through its whole defer window.
    assert (
        _submit_bot(dispatcher, 1, reason=SyncReason.OWNER_HANDOFF)
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        for attempt_no, step in enumerate((1.0, 2.0, 4.0, 8.0, None), start=1):
            await spin_until(
                lambda expected=attempt_no: len(service.hold_calls) == expected,
                what=f"bot-1 hold attempt {attempt_no}",
            )
            if step is not None:
                clock.advance(step)
                dispatcher.wake()
        await spin_until(
            lambda: dispatcher.stats.entries == 0,
            what="bot-1 entry dropped after its window",
        )
        assert dispatcher.stats.latched_owner_keys == 1

        # Latch retention via public admission behavior: the latched key
        # re-admits (one free slot — the entry-latched overlap counts
        # once) and routes straight to the hold, never a roster send.
        assert _submit_bot(dispatcher, 1) is SubmissionResult.QUEUED
        await spin_until(
            lambda: len(service.hold_calls) == 6, what="latched key routed to hold"
        )
        assert service.sync_once_calls == []

        # The second failed hold fills the remaining slot.
        assert (
            _submit_bot(dispatcher, 2, reason=SyncReason.OWNER_HANDOFF)
            is SubmissionResult.QUEUED
        )
        await spin_until(
            lambda: len(service.hold_calls) == 7, what="bot-2 first hold attempt"
        )
        # Both entries sit in fresh defer windows — drive them to expiry.
        for expected, step in ((9, 1.0), (11, 2.0), (13, 4.0), (15, 8.0)):
            clock.advance(step)
            dispatcher.wake()
            await spin_until(
                lambda e=expected: len(service.hold_calls) == e,
                what=f"paired hold attempt {expected}",
            )
        await spin_until(
            lambda: dispatcher.stats.entries == 0,
            what="both failed-hold entries drained",
        )
        assert dispatcher.stats.latched_owner_keys == 2

        # Two latches, zero entries: the budget is full — fail closed.
        with caplog.at_level(logging.WARNING):
            assert _submit_bot(dispatcher, 3) is SubmissionResult.REJECTED
        assert diagnostics.counted[("submit_rejected", "queue_full")] == 1
        warnings = _dispatch_warnings(caplog, "submit_rejected")
        assert len(warnings) == 1
        assert "reason=queue_full" in warnings[0]
        assert "scope=submission" in warnings[0]
        assert service.sync_once_calls == []  # owner protection throughout
    finally:
        await dispatcher.stop()


# -- space expansion: capacity truncation is one recorded event ------------------


@pytest.mark.asyncio
async def test_space_expansion_truncation_records_once(caplog):
    """A hint paged into a full queue stops mid-expansion with one
    diagnostic + one WARNING (reason, space scope, spaces/admitted
    counts, compensation hint) — the truncated remainder is never
    enumerated, and per-key fan-out drops do not become a log storm."""
    service = ScriptedService()
    reader = PagedReader(
        [
            (
                SyncKey("a", "dev", 21),
                SyncKey("a", "dev", 22),
                SyncKey("a", "dev", 23),
                SyncKey("a", "dev", 24),
                SyncKey("a", "dev", 25),
            )
        ]
    )
    diagnostics = SyncDiagnostics()
    dispatcher = BotManagerSyncDispatcher(
        service,
        reader,
        policy=DispatcherPolicy(worker_count=1, max_keys=3, page_size=5),
        diagnostics=diagnostics,
    )
    assert (
        dispatcher.request_space_sync(SpaceChange("a", "dev", ("space:1",), ()))
        is SubmissionResult.QUEUED
    )
    await dispatcher.attach()
    try:
        with caplog.at_level(logging.WARNING):
            assert await dispatcher.drain(5.0) is True
        assert dispatcher.stats.rejected_space_keys == 2  # keys 24, 25
        assert diagnostics.counted[("space_expansion_truncated", "queue_full")] == 1
        # Fan-out key drops aggregate into the one truncation record.
        assert ("submit_rejected", "queue_full") not in diagnostics.counted
        warnings = _dispatch_warnings(caplog, "space_expansion_truncated")
        assert len(warnings) == 1
        message = warnings[0]
        assert "reason=queue_full" in message
        assert "scope=space-fanout" in message
        assert "spaces=1" in message
        assert "admitted=3" in message
        assert "repair sweep" in message
        # The truncated remainder is never enumerated in the line.
        assert "24" not in message
        assert "25" not in message
    finally:
        await dispatcher.stop()
