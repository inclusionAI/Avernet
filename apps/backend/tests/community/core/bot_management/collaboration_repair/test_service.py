"""One-shot repair orchestration — per-item outcomes in a bounded request.

Unit level: Mock readers + AsyncMock delivery plugins, all wired through the
public service constructor by ``factories.make_repair_case``. The suite pins
the plan's behaviour contract:

- fixed execution order (facts read → owner precondition → exists → ownership
  read/write → profile → fresh managers read/write), asserted
  from the actual call order, not final state;
- exactly one bounded send per component and one exists probe;
- Owner missing-noop, drift, deadline and delivery-classification mappings;
- preconditions raised before any outbound never produce a report;
- initial facts errors raise; execution facts errors preserve the report;
- cancellation propagates and starts no new send;
- no queue submission surface, no credential/runtime writes.
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import patch

import pytest

from agentclaw.community.kernel.bot_manager_sync import (
    BOT_NOT_FOUND_CODE,
    CollaboratorFact,
    DeliveryFailure,
    SnapshotTooLarge,
    SnapshotUnavailable,
    SyncFacts,
    SyncKey,
)
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipFacts,
)
from agentclaw.community.kernel.collaboration_repair import (
    MetadataDeliveryError,
    MetadataDeliveryErrorCode,
    RepairCommand,
    RepairFactsError,
    RepairPreconditionError,
    RepairPreconditionErrorCode,
)
from agentclaw.community.kernel.collaboration_repair import (
    RepairFactsErrorCode as FactsCode,
)
from agentclaw.community.plugin_api.bcs_managers import BcsManagersError
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipError


from tests.community.core.bot_management.collaboration_repair.factories import (
    CURRENT_OWNER,
    KEY,
    STABLE,
    default_repair_facts,
    make_repair_case,
)

_SERVICE_MODULE = (
    "agentclaw.community.core.bot_management.collaboration_repair.service"
)

# The plan's RED test relies on this fixture; it is registered here
# explicitly ("显式 fixture import" route) from the factories module.


@pytest.fixture
def repair_case():
    return make_repair_case()


def _names(case) -> list[str]:
    """The ordered interaction names of one repair run, readers included."""
    return [entry[0] for entry in case.calls.mock_calls]


def _items(report) -> dict[str, tuple[str, str | None]]:
    return {
        item.component: (item.status, item.error_code) for item in report.items
    }


def _assert_once_per_component(case) -> None:
    assert case.metadata.exists.await_count == 1
    assert case.ownership.replace_ownership.await_count == 1
    assert case.metadata.update_profile.await_count == 1
    assert case.managers.replace_managers.await_count == 1
    assert case.reader.read_facts.call_count == 1
    assert case.ownership_reader.read_facts.call_count == 1
    assert case.managers_reader.read_facts.call_count == 1


# ── The plan's RED test — preconditions raise before any outbound write ───


@pytest.mark.asyncio
async def test_missing_target_sends_no_writes(repair_case):
    repair_case.metadata.exists.return_value = False
    with pytest.raises(RepairPreconditionError, match="collaboration_bot_not_found"):
        await repair_case.service.repair(repair_case.command)
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.managers.replace_managers.assert_not_awaited()
    repair_case.metadata.update_profile.assert_not_awaited()


# ── Happy path: order, one send per component, real projector behaviour ───


@pytest.mark.asyncio
async def test_full_success_order_reads_fresh_and_sends_once_per_component(
    repair_case,
):
    report = await repair_case.service.repair(repair_case.command)

    # Actual call order: the managers facts are (re)read only after the
    # ownership write and the profile send.
    assert _names(repair_case) == [
        "reader.read_facts",
        "metadata.exists",
        "ownership_reader.read_facts",
        "ownership.replace_ownership",
        "metadata.update_profile",
        "managers_reader.read_facts",
        "managers.replace_managers",
    ]
    _assert_once_per_component(repair_case)

    # Ownership: fresh ownership facts, projected with the stable address.
    (ownership_snapshot,) = [
        call.args[0] for call in repair_case.ownership.replace_ownership.await_args_list
    ]
    assert ownership_snapshot.bcs_bot_id == STABLE
    assert ownership_snapshot.owner_user_id == CURRENT_OWNER
    assert ownership_snapshot.key == KEY

    # Profile: the frozen snapshot from the initial facts read, verbatim.
    (profile,) = [
        call.args[0] for call in repair_case.metadata.update_profile.await_args_list
    ]
    assert profile is repair_case.facts.profile

    # Managers: the fresh re-read, owner-excluded and space-filtered by the
    # real projector, sorted and deduped.
    (manager_snapshot,) = [
        call.args[0] for call in repair_case.managers.replace_managers.await_args_list
    ]
    assert manager_snapshot.bcs_bot_id == STABLE
    assert manager_snapshot.owner_id == CURRENT_OWNER
    assert manager_snapshot.manager_user_ids == repair_case.expected_roster

    assert report.bot_id == repair_case.facts.profile.bot_id
    assert report.status == "succeeded"
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": ("succeeded", None),
        "managers": ("succeeded", None),
    }
    assert tuple(item.component for item in report.items) == (
        "ownership",
        "profile",
        "managers",
    )


@pytest.mark.asyncio
async def test_managers_replacement_uses_fresh_facts(repair_case):
    """The delivered roster tracks the re-read, not the pre-write state."""
    fresh = SyncFacts(
        KEY,
        "b",
        "e",
        CURRENT_OWNER,
        (CollaboratorFact("updated-1", "member", True),),
        bot_type="service",
    )
    repair_case.managers_reader.read_facts.return_value = fresh
    report = await repair_case.service.repair(repair_case.command)
    (manager_snapshot,) = [
        call.args[0] for call in repair_case.managers.replace_managers.await_args_list
    ]
    assert manager_snapshot.manager_user_ids == ("updated-1",)
    assert _items(report)["managers"] == ("succeeded", None)


@pytest.mark.asyncio
async def test_owner_exclusion_and_space_rules_come_from_real_projector(
    repair_case,
):
    """The service ships the real roster: owner and ineligible rows dropped.

    The manager set rules (owner exclusion, space eligibility, admin/member
    roles only, cap, sort and dedupe) are the projector's contract; the
    service must carry them, not re-implement them.
    """
    await repair_case.service.repair(repair_case.command)
    (manager_snapshot,) = [
        call.args[0] for call in repair_case.managers.replace_managers.await_args_list
    ]
    assert CURRENT_OWNER not in manager_snapshot.manager_user_ids
    assert "ineligible" not in manager_snapshot.manager_user_ids
    assert manager_snapshot.manager_user_ids == repair_case.expected_roster


# ── Preconditions (owner/exists/deadline) — before any outbound write ───


@pytest.mark.asyncio
async def test_actor_drift_raises_owner_changed_before_any_outbound(repair_case):
    repair_case.command = RepairCommand(KEY, "owner-old")
    with pytest.raises(RepairPreconditionError, match="owner_changed"):
        await repair_case.service.repair(repair_case.command)
    assert repair_case.reader.read_facts.call_count == 1
    repair_case.metadata.exists.assert_not_awaited()
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.managers.replace_managers.assert_not_awaited()
    repair_case.metadata.update_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_exists_unavailable_maps_to_sync_unavailable_precondition(
    repair_case,
):
    repair_case.metadata.exists.side_effect = MetadataDeliveryError(
        DeliveryFailure.UNKNOWN,
        MetadataDeliveryErrorCode.COLLABORATION_SYNC_UNAVAILABLE,
    )
    with pytest.raises(
        RepairPreconditionError, match="collaboration_sync_unavailable"
    ):
        await repair_case.service.repair(repair_case.command)
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.metadata.update_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_exists_timeout_maps_to_deadline_precondition(repair_case):
    repair_case.metadata.exists.side_effect = MetadataDeliveryError(
        DeliveryFailure.UNKNOWN, MetadataDeliveryErrorCode.DELIVERY_TIMEOUT
    )
    with pytest.raises(RepairPreconditionError, match="repair_deadline_exceeded"):
        await repair_case.service.repair(repair_case.command)
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_exists_gate_precondition_passes_through(repair_case):
    repair_case.metadata.exists.side_effect = RepairPreconditionError(
        RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )
    with pytest.raises(
        RepairPreconditionError, match="collaboration_sync_unavailable"
    ):
        await repair_case.service.repair(repair_case.command)
    repair_case.metadata.update_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_exists_probe_backstop_expiry_is_a_precondition():
    """The outer ``wait_for`` backstop, not a delivery error, trips first."""
    never = asyncio.Event()

    async def stuck(bot_id, *, timeout_seconds):
        await never.wait()

    case = make_repair_case(timeout_seconds=0.02)
    case.metadata.exists.side_effect = stuck
    with pytest.raises(RepairPreconditionError, match="repair_deadline_exceeded"):
        await case.service.repair(case.command)
    case.ownership.replace_ownership.assert_not_awaited()
    case.metadata.update_profile.assert_not_awaited()
    case.managers.replace_managers.assert_not_awaited()
    never.set()


@pytest.mark.asyncio
async def test_exhausted_deadline_at_entry_is_a_precondition():
    case = make_repair_case(timeout_seconds=0.0)
    with pytest.raises(RepairPreconditionError, match="repair_deadline_exceeded"):
        await case.service.repair(case.command)
    case.reader.read_facts.assert_not_called()
    case.metadata.exists.assert_not_awaited()


# ── Ownership item: one send, return interpreted, anomalies stop the tail ───


@pytest.mark.asyncio
async def test_missing_bot_noop_is_reported_target_gone_not_success(repair_case):
    """OwnershipChange.owner_user_id is None — BCS knows no such Bot."""
    repair_case.ownership.replace_ownership.return_value = OwnershipChange(
        STABLE, None, None, False, "req-1"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report) == {
        "ownership": ("failed", "collaboration_bot_not_found"),
        "profile": ("skipped_dependency", None),
        "managers": ("skipped_dependency", None),
    }
    assert report.status == "failed"
    repair_case.metadata.update_profile.assert_not_awaited()
    repair_case.managers_reader.read_facts.assert_not_called()
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_ownership_confirmation_for_another_bot_is_unconfirmed(repair_case):
    repair_case.ownership.replace_ownership.return_value = OwnershipChange(
        "x:y", CURRENT_OWNER, None, False, "req-1"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["ownership"] == (
        "unknown",
        "collaboration_response_malformed",
    )
    repair_case.metadata.update_profile.assert_not_awaited()
    repair_case.managers.replace_managers.assert_not_awaited()
    assert report.status == "failed"


@pytest.mark.asyncio
async def test_ownership_owner_mismatch_reports_owner_drift(repair_case):
    repair_case.ownership.replace_ownership.return_value = OwnershipChange(
        STABLE, "owner-other", "owner-old", True, "req-1"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["ownership"] == ("failed", "owner_drift")
    assert _items(report)["managers"] == ("skipped_dependency", None)
    assert report.status == "failed"


@pytest.mark.asyncio
async def test_ownership_already_aligned_reports_succeeded(repair_case):
    """``changed=False`` is a confirmed no-op, not a failure."""
    repair_case.ownership.replace_ownership.return_value = OwnershipChange(
        STABLE, CURRENT_OWNER, None, False, "req-1"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["ownership"] == ("succeeded", None)
    assert report.status == "succeeded"


@pytest.mark.asyncio
async def test_fresh_committed_owner_is_adopted_and_repair_continues():
    """Facts read after the precondition are authoritative for the owner.

    The actor matched the owner at the initial read, so the request is
    authorized; a handoff that committed before the fresh ownership read
    means the fresh owner is what gets written, and the rest of the
    repair follows the same fresh facts.
    """
    facts = default_repair_facts(owner_user_id="owner-a")
    fresh_owner = "owner-b"
    case = make_repair_case(
        facts=facts,
        ownership_facts=OwnershipFacts(KEY, "b", "e", fresh_owner, bot_type="service"),
        manager_facts=SyncFacts(KEY, "b", "e", fresh_owner, (), bot_type="service"),
    )
    report = await case.service.repair(case.command)
    (snapshot,) = [
        call.args[0] for call in case.ownership.replace_ownership.await_args_list
    ]
    assert snapshot.owner_user_id == fresh_owner
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": ("succeeded", None),
        "managers": ("succeeded", None),
    }
    assert report.status == "succeeded"


@pytest.mark.asyncio
async def test_ownership_projector_refusal_is_a_facts_error(repair_case):
    """A projection that cannot validate identities is never a send."""
    with patch.object(
        importlib.import_module(_SERVICE_MODULE),
        "build_ownership_snapshot",
        side_effect=SnapshotUnavailable("missing_entity_id"),
    ):
        with pytest.raises(RepairFactsError) as excinfo:
            await repair_case.service.repair(repair_case.command)
    assert excinfo.value.code == FactsCode.COLLABORATION_FACTS_READ_ERROR
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.metadata.update_profile.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, status",
    [
        (DeliveryFailure.NOT_SENT, "failed"),
        (DeliveryFailure.REJECTED, "failed"),
        (DeliveryFailure.UNKNOWN, "unknown"),
    ],
)
async def test_ownership_delivery_classification_flows_onto_the_item(
    repair_case, kind, status
):
    repair_case.ownership.replace_ownership.side_effect = BcsOwnershipError(
        kind, "timeout"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["ownership"] == (status, "timeout")
    repair_case.metadata.update_profile.assert_not_awaited()
    repair_case.managers_reader.read_facts.assert_not_called()
    repair_case.managers.replace_managers.assert_not_awaited()
    assert report.status == "failed"


# ── Profile item: failure continues; target-gone stops the tail ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, code, status",
    [
        (
            DeliveryFailure.UNKNOWN,
            MetadataDeliveryErrorCode.COLLABORATION_UPSTREAM_ERROR,
            "unknown",
        ),
        (
            DeliveryFailure.REJECTED,
            MetadataDeliveryErrorCode.COLLABORATION_REJECTED,
            "failed",
        ),
    ],
)
async def test_profile_failure_continues_to_managers(
    repair_case, kind, code, status
):
    repair_case.metadata.update_profile.side_effect = MetadataDeliveryError(kind, code)
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": (status, code),
        "managers": ("succeeded", None),
    }
    assert report.status == "partial_failed"
    assert repair_case.managers.replace_managers.await_count == 1


@pytest.mark.asyncio
async def test_profile_target_gone_stops_managers(repair_case):
    repair_case.metadata.update_profile.side_effect = MetadataDeliveryError(
        DeliveryFailure.REJECTED, MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": ("failed", "collaboration_bot_not_found"),
        "managers": ("skipped_dependency", None),
    }
    assert report.status == "partial_failed"
    repair_case.managers_reader.read_facts.assert_not_called()
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_deadline_exhaustion_stops_managers(repair_case):
    """The plan's profile continuation rule has two exceptions only."""
    repair_case.metadata.update_profile.side_effect = MetadataDeliveryError(
        DeliveryFailure.UNKNOWN, MetadataDeliveryErrorCode.DELIVERY_TIMEOUT
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": ("unknown", "delivery_timeout"),
        "managers": ("skipped_dependency", None),
    }
    assert report.status == "partial_failed"
    repair_case.managers_reader.read_facts.assert_not_called()
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_env_gate_precondition_becomes_item_failure(repair_case):
    """An adapter gate refusal is definitively not sent — a failed item."""
    repair_case.metadata.update_profile.side_effect = RepairPreconditionError(
        RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["profile"] == (
        "failed",
        "collaboration_sync_unavailable",
    )
    assert _items(report)["managers"] == ("succeeded", None)
    assert report.status == "partial_failed"


# ── Managers item: fresh read, drift, facts errors ──────────────────────


@pytest.mark.asyncio
async def test_owner_drift_after_ownership_write_skips_stale_managers(repair_case):
    drifted = SyncFacts(KEY, "b", "e", "owner-newer", (), bot_type="service")
    repair_case.managers_reader.read_facts.return_value = drifted
    report = await repair_case.service.repair(repair_case.command)

    # Never write the stale roster: the drift is discovered at the managers
    # step and carried there without changing the roster.
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": ("succeeded", None),
        "managers": ("skipped_dependency", "owner_drift"),
    }
    assert report.status == "partial_failed"
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reader_error, expected_code",
    [
        (
            SnapshotUnavailable("read_error"),
            FactsCode.COLLABORATION_FACTS_READ_ERROR,
        ),
        (
            SnapshotUnavailable(BOT_NOT_FOUND_CODE),
            FactsCode.COLLABORATION_BOT_NOT_FOUND,
        ),
    ],
)
async def test_managers_facts_error_reports_never_empty_roster(
    repair_case, reader_error, expected_code
):
    repair_case.managers_reader.read_facts.side_effect = reader_error
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["managers"] == ("failed", expected_code)
    assert report.status == "partial_failed"
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_managers_unclassified_reader_error_still_raises(repair_case):
    repair_case.managers_reader.read_facts.side_effect = RuntimeError(
        "private db detail"
    )
    with pytest.raises(RuntimeError, match="private db detail"):
        await repair_case.service.repair(repair_case.command)
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_managers_delivery_failure_is_reported(repair_case):
    repair_case.managers.replace_managers.side_effect = BcsManagersError(
        DeliveryFailure.REJECTED, "http_403"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": ("succeeded", None),
        "managers": ("failed", "http_403"),
    }
    assert report.status == "partial_failed"


@pytest.mark.asyncio
async def test_managers_delivery_unknown_maps_item_unknown(repair_case):
    repair_case.managers.replace_managers.side_effect = BcsManagersError(
        DeliveryFailure.UNKNOWN, "read_timeout"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["managers"] == ("unknown", "read_timeout")


@pytest.mark.asyncio
async def test_ownership_facts_error_raises_before_any_write(repair_case):
    repair_case.ownership_reader.read_facts.side_effect = SnapshotUnavailable(
        "bot_not_found"
    )
    with pytest.raises(RepairFactsError, match="collaboration_bot_not_found"):
        await repair_case.service.repair(repair_case.command)
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.metadata.update_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_ownership_identity_unresolved_mid_flight_raises(repair_case):
    """A stable address that moved between reads is an identity error."""
    repair_case.ownership_reader.read_facts.return_value = OwnershipFacts(
        KEY, "b", "e2", CURRENT_OWNER, bot_type="service"
    )
    with pytest.raises(
        RepairFactsError, match="collaboration_bot_identity_unresolved"
    ):
        await repair_case.service.repair(repair_case.command)
    repair_case.ownership.replace_ownership.assert_not_awaited()


@pytest.mark.asyncio
async def test_managers_identity_unresolved_mid_flight_reports_failure(repair_case):
    repair_case.managers_reader.read_facts.return_value = SyncFacts(
        KEY, "b", "e2", CURRENT_OWNER, (), bot_type="service"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["managers"] == (
        "failed", "collaboration_bot_identity_unresolved"
    )
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_manager_facts_are_identity_not_benign_drift(repair_case):
    """A foreign Bot is an identity error, even if its owner also differs.

    Order matters: identity is validated before the owner-drift comparison,
    so facts describing another Bot never masquerade as a quiet
    ``skipped_dependency``/owner_drift on ours.
    """
    repair_case.managers_reader.read_facts.return_value = SyncFacts(
        KEY, "b", "e2", "owner-newer", (), bot_type="service"
    )
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["managers"] == (
        "failed", "collaboration_bot_identity_unresolved"
    )
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_managers_reader_oversized_roster_reports_failure_never_empty(repair_case):
    """``SnapshotTooLarge`` from the reader is "unreadable", not "empty"."""
    repair_case.managers_reader.read_facts.side_effect = SnapshotTooLarge()
    report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["managers"] == ("failed", FactsCode.COLLABORATION_FACTS_READ_ERROR)
    repair_case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_managers_projector_refusal_preserves_prior_writes(repair_case):
    """A projector refusal surfaces as a facts error; earlier sends stand.

    Ownership and profile remain in the report; an unreadable roster
    must not become an empty one.
    """
    with patch.object(
        importlib.import_module(_SERVICE_MODULE),
        "build_manager_snapshot",
        side_effect=SnapshotUnavailable("space_unavailable"),
    ):
        report = await repair_case.service.repair(repair_case.command)
    assert _items(report)["managers"] == ("failed", FactsCode.COLLABORATION_FACTS_READ_ERROR)
    repair_case.managers.replace_managers.assert_not_awaited()
    repair_case.ownership.replace_ownership.assert_awaited_once()
    repair_case.metadata.update_profile.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_owner_only_roster_is_a_successful_repair(repair_case):
    """``manager_user_ids == ()`` is a real desired state, not a failure.

    The background sync special-cases this as ACKNOWLEDGED_EMPTY; the
    single-shot repair has no queue and no such event, so a confirmed
    empty replace is simply success.
    """
    repair_case.managers_reader.read_facts.return_value = SyncFacts(
        KEY, "b", "e", CURRENT_OWNER, (), bot_type="service"
    )
    report = await repair_case.service.repair(repair_case.command)
    (snapshot,) = [
        call.args[0] for call in repair_case.managers.replace_managers.await_args_list
    ]
    assert snapshot.manager_user_ids == ()
    assert _items(report)["managers"] == ("succeeded", None)
    assert report.status == "succeeded"


@pytest.mark.asyncio
async def test_initial_facts_key_mismatch_is_a_facts_error(repair_case):
    other_key = SyncKey("tenant-a", "dev", 8)
    repair_case.reader.read_facts.return_value = default_repair_facts(
        key=other_key
    )
    with pytest.raises(RepairFactsError) as excinfo:
        await repair_case.service.repair(repair_case.command)
    assert excinfo.value.code == FactsCode.COLLABORATION_FACTS_READ_ERROR
    repair_case.metadata.exists.assert_not_awaited()


# ── Deadline semantics mid-execution ────────────────────────────────────


@pytest.mark.asyncio
async def test_deadline_between_profile_and_managers_fails_managers():
    """An item never sent is failed-with-timeout, never unknown."""
    case = make_repair_case()

    def trip_after_profile(snapshot, *, timeout_seconds):
        assert snapshot is case.facts.profile
        case.clock.now += case.timeout_seconds + 1
        return None

    case.metadata.update_profile.side_effect = trip_after_profile
    report = await case.service.repair(case.command)
    assert _items(report) == {
        "ownership": ("succeeded", None),
        "profile": ("succeeded", None),
        "managers": ("failed", "delivery_timeout"),
    }
    assert report.status == "partial_failed"
    case.managers.replace_managers.assert_not_awaited()
    case.managers_reader.read_facts.assert_not_called()


@pytest.mark.asyncio
async def test_deadline_before_ownership_send_fails_first_item():
    case = make_repair_case()

    def trip_after_read(key):
        case.clock.now += case.timeout_seconds + 1
        return case.ownership_reader.read_facts.return_value

    case.ownership_reader.read_facts.side_effect = trip_after_read
    report = await case.service.repair(case.command)
    assert _items(report) == {
        "ownership": ("failed", "delivery_timeout"),
        "profile": ("skipped_dependency", None),
        "managers": ("skipped_dependency", None),
    }
    assert report.status == "failed"
    case.ownership.replace_ownership.assert_not_awaited()
    case.metadata.update_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_in_flight_budget_expiry_marks_unknown_not_failed():
    """An outer-budget expiry during a started send is unconfirmed."""
    case = make_repair_case(timeout_seconds=0.05)

    async def slow(snapshot, *, timeout_seconds):
        await asyncio.sleep(0.5)
        return case.ownership.replace_ownership.return_value

    case.ownership.replace_ownership.side_effect = slow
    report = await case.service.repair(case.command)
    assert _items(report)["ownership"] == ("unknown", "delivery_timeout")
    assert _items(report)["managers"] == ("skipped_dependency", None)
    assert report.status == "failed"


# ── Cancellation: propagate; start no new send ──────────────────────────


@pytest.mark.asyncio
async def test_cancellation_during_probe_sends_nothing(repair_case):
    never = asyncio.Event()

    async def stuck(bot_id, *, timeout_seconds):
        await never.wait()

    repair_case.metadata.exists.side_effect = stuck
    task = asyncio.create_task(repair_case.service.repair(repair_case.command))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.metadata.update_profile.assert_not_awaited()
    repair_case.managers.replace_managers.assert_not_awaited()
    never.set()


@pytest.mark.asyncio
async def test_cancellation_during_in_flight_ownership_starts_no_new_send(
    repair_case,
):
    never = asyncio.Event()

    async def stuck(snapshot, *, timeout_seconds):
        await never.wait()

    repair_case.ownership.replace_ownership.side_effect = stuck
    task = asyncio.create_task(repair_case.service.repair(repair_case.command))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    repair_case.metadata.update_profile.assert_not_awaited()
    repair_case.managers_reader.read_facts.assert_not_called()
    repair_case.managers.replace_managers.assert_not_awaited()
    never.set()
