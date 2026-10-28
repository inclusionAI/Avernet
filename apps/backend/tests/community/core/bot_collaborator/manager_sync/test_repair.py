"""Unit tests for ManagerSyncRepairService (Task 10: controlled recovery).

Pins the repair surface — first backfill, targeted compensation, and
marker recovery — the maintenance-only operations that must never behave
like a business entry point:

- ``request_bot`` only delegates to the unified notifier with the REPAIR
  reason: REJECTED / DISABLED / COALESCED are surfaced verbatim and never
  dressed up as confirmed syncs.
- ``request_page`` sweeps with the reader's explicit tenant/env scoping,
  clamps the page to 100, and preserves every key the notifier refused —
  a caller may not blind-advance the cursor and lose failed entries.
- ``clear_confirmed_attempt`` demands non-empty operator + evidence
  *before* touching the coordinator, passes the exact attempt id to the
  CAS clear, returns the coordinator's bool verbatim — a refused CAS has
  zero downstream effect and an infrastructure failure propagates (the
  operator must see it; never a fabricated True).
- compensation reads the *current* database, never a historical roster:
  repair projects nothing itself, it only re-submits the keys a fresh
  page read returns.

No test touches Redis, HTTP, or BCS: repair is a maintenance context and
everything behind it is doubled with Mocks.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.repair import (
    DisabledManagerSyncRepair,
    ManagerSyncRepairService,
    RepairPage,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncReason,
)
from agentclaw.community.plugin_api.manager_sync_coordination import (
    CoordinationUnavailable,
)


# -- controlled recovery: exact attempt_id CAS -------------------------------


def test_repair_never_clears_a_different_attempt():
    coordinator, notifier, reader = Mock(), Mock(), Mock()
    coordinator.clear_confirmed.return_value = False
    repair = ManagerSyncRepairService(reader, notifier, coordinator)
    key = SyncKey("a", "dev", 7)
    assert not repair.clear_confirmed_attempt(key, attempt_id="stale", operator_id="operator",
                                              evidence="incident-123 remote drain confirmed")
    notifier.request_sync.assert_not_called()


def test_clear_rejects_empty_audit_fields_before_touching_coordinator():
    coordinator, notifier, reader = Mock(), Mock(), Mock()
    repair = ManagerSyncRepairService(reader, notifier, coordinator)
    key = SyncKey("t", "dev", 7)
    for bad in ("", "   \t "):
        with pytest.raises(ValueError, match="operator_id"):
            repair.clear_confirmed_attempt(
                key, attempt_id="att-1", operator_id=bad,
                evidence="INC-9 BCS audit shows no further submits",
            )
        with pytest.raises(ValueError, match="evidence"):
            repair.clear_confirmed_attempt(
                key, attempt_id="att-1", operator_id="operator", evidence=bad
            )
    coordinator.clear_confirmed.assert_not_called()
    notifier.request_sync.assert_not_called()


def test_clear_confirmed_returns_cas_success_verbatim_without_auto_send():
    coordinator, notifier, reader = Mock(), Mock(), Mock()
    coordinator.clear_confirmed.return_value = True
    repair = ManagerSyncRepairService(reader, notifier, coordinator)
    key = SyncKey("t", "dev", 7)
    result = repair.clear_confirmed_attempt(
        key, attempt_id="att-42", operator_id="operator",
        evidence="INC-9 BCS audit shows no further submits",
    )
    assert result is True
    coordinator.clear_confirmed.assert_called_once_with(key, attempt_id="att-42")
    # The re-request after a clear is the operator's explicit next step
    # (runbook §5): a clear never hides a send behind it.
    notifier.request_sync.assert_not_called()


def test_clear_failure_propagates_and_never_reports_true():
    coordinator = Mock()
    coordinator.clear_confirmed.side_effect = CoordinationUnavailable("redis gone")
    repair = ManagerSyncRepairService(Mock(), Mock(), coordinator)
    with pytest.raises(CoordinationUnavailable):
        repair.clear_confirmed_attempt(
            SyncKey("t", "dev", 7), attempt_id="att-7", operator_id="operator",
            evidence="INC-7 coordinator unreachable mid-maintenance",
        )
    # The raise is the only outcome — repair must never turn an infra
    # failure into a confirmed True.


# -- targeted compensation: one Bot ------------------------------------------


def test_request_bot_delegates_to_unified_notifier_verbatim():
    reader, coordinator = Mock(), Mock()
    key = SyncKey("t", "dev", 11)
    for verdict in (
        SubmissionResult.QUEUED,
        SubmissionResult.COALESCED,
        SubmissionResult.REJECTED,  # full queue / closed admission / env gate
        SubmissionResult.DISABLED,  # never counted as success by the caller
    ):
        notifier = Mock()
        notifier.request_sync.return_value = verdict
        repair = ManagerSyncRepairService(reader, notifier, coordinator)
        assert repair.request_bot(key) is verdict
        notifier.request_sync.assert_called_once_with(
            bot_pk=11, env="dev", avernet_tenant="t", reason=SyncReason.REPAIR
        )


def test_unconfirmed_marker_is_never_cleared_by_request_bot():
    """Compensation without a confirmed clear: a marker keeps blocking —
    request_bot only submits, it never touches the coordinator."""
    coordinator, reader = Mock(), Mock()
    notifier = Mock()
    notifier.request_sync.return_value = SubmissionResult.REJECTED
    repair = ManagerSyncRepairService(reader, notifier, coordinator)
    assert repair.request_bot(SyncKey("t", "dev", 7)) is SubmissionResult.REJECTED
    coordinator.clear_confirmed.assert_not_called()
    coordinator.finish.assert_not_called()
    coordinator.release.assert_not_called()


# -- first backfill: bounded page sweep ---------------------------------------


def test_request_page_clamps_limit_to_one_hundred():
    reader = Mock()
    reader.page_keys.return_value = ()
    repair = ManagerSyncRepairService(reader, Mock(), Mock())
    page = repair.request_page(
        avernet_tenant="t", env="dev", after_pk=0, limit=500
    )
    reader.page_keys.assert_called_once_with(
        avernet_tenant="t", env="dev", after_pk=0, limit=100
    )
    assert page == RepairPage(last_pk=0, queued=0, coalesced=0, rejected=0,
                             rejected_keys=())


def test_request_page_empty_page_keeps_cursor_and_zero_counts():
    reader = Mock()
    reader.page_keys.return_value = ()
    repair = ManagerSyncRepairService(reader, Mock(), Mock())
    page = repair.request_page(
        avernet_tenant="t", env="dev", after_pk=41, limit=100
    )
    # An empty page is the reader's "no more keys" signal: the sweep ends,
    # the cursor is untouched, nothing was silently dropped.
    assert page == RepairPage(last_pk=41, queued=0, coalesced=0, rejected=0,
                              rejected_keys=())


def test_request_page_counts_and_preserves_rejected_keys_precisely():
    keys = (
        SyncKey("t", "dev", 101),
        SyncKey("t", "dev", 102),
        SyncKey("t", "dev", 103),
        SyncKey("t", "dev", 104),
    )
    reader = Mock()
    reader.page_keys.return_value = keys
    notifier = Mock()
    notifier.request_sync.side_effect = [
        SubmissionResult.QUEUED,    # 101 submitted
        SubmissionResult.COALESCED,  # 102 already queued — nothing to redo
        SubmissionResult.REJECTED,   # 103 dropped at admission
        SubmissionResult.DISABLED,   # 104 feature posture refused it
    ]
    repair = ManagerSyncRepairService(reader, notifier, Mock())
    page = repair.request_page(
        avernet_tenant="t", env="dev", after_pk=100, limit=100
    )
    assert page == RepairPage(
        last_pk=104, queued=1, coalesced=1, rejected=2,
        rejected_keys=(keys[2], keys[3]),
    )
    # A rejection never aborts the page: later keys are still submitted.
    assert notifier.request_sync.call_count == 4


# -- tenant isolation ----------------------------------------------------------


def test_request_page_scopes_tenant_and_env_explicitly():
    keys = (SyncKey("tenant-b", "prod", 21), SyncKey("tenant-b", "prod", 22))
    reader = Mock()
    reader.page_keys.return_value = keys
    notifier = Mock()
    notifier.request_sync.return_value = SubmissionResult.QUEUED
    repair = ManagerSyncRepairService(reader, notifier, Mock())
    repair.request_page(
        avernet_tenant="tenant-b", env="prod", after_pk=20, limit=50
    )
    # The sweep reads exactly the requested tenant/env page — no ambient
    # request tenant can widen or shift the scope.
    reader.page_keys.assert_called_once_with(
        avernet_tenant="tenant-b", env="prod", after_pk=20, limit=50
    )
    for call, key in zip(notifier.request_sync.call_args_list, keys):
        assert call.kwargs == {
            "bot_pk": key.bot_pk,
            "env": key.env,
            "avernet_tenant": key.avernet_tenant,
            "reason": SyncReason.REPAIR,
        }


# -- compensation reads CURRENT state, not historical rosters -------------------


def test_request_page_compensates_from_current_db_not_historical_rosters():
    reader = Mock()
    reader.page_keys.side_effect = [
        (SyncKey("t", "dev", 1),),
        (SyncKey("t", "dev", 2),),  # the db moved between sweeps: 2 is what
        (SyncKey("t", "dev", 3),),  # exists NOW, not a remembered list
    ]
    notifier = Mock()
    notifier.request_sync.return_value = SubmissionResult.QUEUED
    repair = ManagerSyncRepairService(reader, notifier, Mock())
    first = repair.request_page(
        avernet_tenant="t", env="dev", after_pk=0, limit=100
    )
    assert first == RepairPage(last_pk=1, queued=1, coalesced=0, rejected=0,
                               rejected_keys=())
    notifier.request_sync.assert_called_once_with(
        bot_pk=1, env="dev", avernet_tenant="t", reason=SyncReason.REPAIR
    )
    second = repair.request_page(
        avernet_tenant="t", env="dev", after_pk=1, limit=100
    )
    assert second == RepairPage(last_pk=2, queued=1, coalesced=0, rejected=0,
                                rejected_keys=())
    third = repair.request_page(
        avernet_tenant="t", env="dev", after_pk=2, limit=100
    )
    assert third.last_pk == 3
    # Repair projects nothing itself: each page submits exactly the fresh
    # read's keys, and the roster is built later by the sync round from
    # the current facts — never carried or replayed here.
    reader.read_facts.assert_not_called()
    assert notifier.request_sync.call_count == 3


# -- disabled posture (composition root binds this twin) ------------------------


def test_disabled_posture_answers_disabled_and_refuses_fabrication():
    repair = DisabledManagerSyncRepair(reason="shared-redis-required")
    # Single-Bot delegation answers exactly like the disabled notifier…
    assert repair.request_bot(SyncKey("t", "dev", 1)) is SubmissionResult.DISABLED
    # …but the sweep and the CAS clear refuse loudly: there is no
    # coordinator to answer against, and no fabricated CAS verdict.
    with pytest.raises(RuntimeError, match="shared-redis-required"):
        repair.request_page(avernet_tenant="t", env="dev", after_pk=0, limit=10)
    with pytest.raises(RuntimeError, match="shared-redis-required"):
        repair.clear_confirmed_attempt(
            SyncKey("t", "dev", 1), attempt_id="att-1", operator_id="operator",
            evidence="INC-1",
        )