"""Controlled credentials/runtime; real HTTP, journal, queue, signer and BCS.

These stories do NOT prove real Passport retirement or physical device updates.
Manual repair is separately labelled; it never substitutes for business delivery.
"""
import pytest

pytestmark = pytest.mark.acceptance


def test_committed_handoff_replaces_owner_and_refreshes_managers(owner_case):
    case = owner_case()
    case.add_collaborators()
    case.wait_roster(case.old_owner, {case.new_owner, case.member})
    case.handoff()
    case.assert_committed()
    case.assert_queue_completed()
    case.wait_owner(case.new_owner)
    case.wait_roster(case.new_owner, {case.member})
    case.assert_old_owner_forbidden()
    case.wait_business_event("send_confirmed")
    # Completed status/retry is a business read, not a repair mechanism.
    before = case.business_event_count("send_confirmed")
    case.retry_terminal_handoff()
    case.assert_no_new_business_confirmation(before)


def test_bcs_rejection_does_not_roll_back_handoff_then_manual_repair(owner_case):
    case = owner_case(register_recipient=False)
    case.handoff()
    case.assert_committed()
    case.assert_queue_completed()
    case.wait_business_event("send_rejected")
    assert case.read_owner(case.old_owner) == case.old_owner
    case.register_recipient()
    evidence = case.manual_repair()
    assert evidence["owner_event"] == "send_confirmed"
    case.wait_owner(case.new_owner)
    case.wait_roster(case.new_owner, set())
    case.assert_old_owner_forbidden()
    case.assert_committed()
    case.assert_queue_completed()
    # Explicit idempotent resubmission: another confirmed delivery, same owner.
    second = case.manual_repair()
    assert second["owner_event"] == "send_confirmed"
    case.wait_owner(case.new_owner)
    case.assert_committed()
    case.assert_queue_completed()


def test_missing_bcs_bot_is_noop_not_confirmation(owner_case):
    case = owner_case(register_bot=False)
    case.handoff()
    case.assert_committed()
    case.assert_queue_completed()
    case.wait_business_event("send_bot_missing_noop")
    case.assert_bcs_bot_absent()
    evidence = case.manual_repair()
    assert evidence["owner_event"] == "send_bot_missing_noop"
    assert evidence["manager_events"] == []
    case.assert_bcs_bot_absent()


def test_transient_disconnect_retries_real_delivery_without_rolling_back(owner_case):
    case = owner_case(transient=True)
    case.add_collaborators()
    case.wait_roster(case.old_owner, {case.new_owner, case.member})
    case.handoff()
    case.assert_committed()
    case.assert_queue_completed()
    case.wait_business_event("send_retryable")
    case.wait_owner(case.new_owner)
    case.wait_roster(case.new_owner, {case.member})
    case.wait_business_event("send_confirmed")
    case.assert_one_proxy_drop_then_forward()
    case.assert_old_owner_forbidden()


def test_admin_http_repair_recovers_rejected_owner_sync_and_restores_authority(owner_case):
    case = owner_case(register_recipient=False)
    case.handoff()
    case.assert_committed()
    case.wait_business_event("send_rejected")
    assert case.read_owner(case.old_owner) == case.old_owner

    denied = case.request_operations_repair(actor=case.old_owner)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "ADMIN_REQUIRED"
    missing = case.request_operations_repair(bot_pk=9223372036854775807)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "BOT_NOT_FOUND"

    # No replay of handoff or direct call to Core: the admin submits the real
    # HTTP repair command, which reads Backend state and delivers to real BCS.
    case.register_recipient()
    case.operations_repair()
    case.wait_business_event("send_confirmed")
    case.wait_owner(case.new_owner)
    case.wait_roster(case.new_owner, set())
    case.assert_old_owner_forbidden()
    case.assert_committed()
    case.assert_queue_completed()
    before = case.business_event_count("send_confirmed")
    case.operations_repair()
    case.wait_business_event("send_confirmed", after_count=before)
    case.wait_owner(case.new_owner)
