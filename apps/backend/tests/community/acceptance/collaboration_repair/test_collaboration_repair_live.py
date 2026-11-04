"""Live singlebox coverage for the Bot avatar sync and manual repair interface.

The stories below are what this stack can honestly prove about the two
deliveries of the spec:

1. The daily avatar chain, end to end against the live BCS: the fixture Bot is
   registered on BCS with an explicit avatar through the official admin
   profile channel, Backend's real owner-scoped avatar route edits and clears
   it, and BCS V1 Group *and* Session views (list and get, four readbacks per
   snapshot) serve the current avatar — a set after an edit, ``null`` after
   the explicit clear.

2. The new one-shot manual repair route: on this credential-less public-local
   stack the composition root's capability gate is the real boundary — the
   owner call arrives, is admitted, and is refused with the documented
   ``503 Collaboration repair is unavailable`` before a plugin is built or a
   write attempted. The stories prove from the live stores that the refusal
   writes nothing: BCS Bot rows do not grow (missing-target zero-creation),
   the drifted profile of an existing Bot stays exactly as drifted (no
   partial writes, no silent compensation), no background queue rows appear,
   and a non-owner never gets past the owner-scoped admission.

The repair success path (HTTP 200 four-item report, ownership/profile/
managers read-backs) is exercised where the transport has provider scope —
the Service API, adapter and report contracts are pinned by the module's
unit, contract-conformance and route suites (see
``tests/community/di/test_collaboration_repair_module.py``,
``tests/community/contracts/test_bcs_collaboration_metadata.py`` and
``tests/community/adapters/http/openapi_v1/test_collaboration_repair.py``).
"""
import pytest

pytestmark = pytest.mark.acceptance

_SET_URL = "https://cdn.example.test/colabrep/first.png"
_CLEAR = ""
_SECOND_URL = "https://cdn.example.test/colabrep/second.png"
_REGISTRATION_URL = "https://cdn.example.test/colabrep/registered.png"
_DRIFTED_NAME = "Fixture-drifted collaboration name"
_DRIFTED_AVATAR = "https://cdn.example.test/colabrep/stale.png"


def test_avatar_registration_daily_sync_and_v1_readback(repair_case):
    """BCS keeps the registered avatar; every Backend edit reaches the V1 views."""
    case = repair_case(register_avatar_url=_REGISTRATION_URL)
    case.create_group_and_session()

    # The official registration channel stored the explicit avatar.
    assert case.registry_row() == {"name": f"Story bot {case.bot_id}", "avatar_url": _REGISTRATION_URL}
    assert case.v1_group_list_avatar() == _REGISTRATION_URL
    assert case.v1_group_detail_avatar() == _REGISTRATION_URL
    assert case.v1_session_list_avatar() == _REGISTRATION_URL
    assert case.v1_session_detail_avatar() == _REGISTRATION_URL

    # Backend's owner-scoped avatar edit is a real daily sync: the committed
    # Backend fact reaches BCS through the transport the edit route drives.
    case.put_avatar(_SET_URL)
    assert case.registry_row()["avatar_url"] == _SET_URL
    assert case.v1_group_list_avatar() == _SET_URL
    assert case.v1_group_detail_avatar() == _SET_URL
    assert case.v1_session_list_avatar() == _SET_URL
    assert case.v1_session_detail_avatar() == _SET_URL

    # The explicit clear ("") is a fact, not a hidden fallback to a stale URL.
    case.put_avatar(_CLEAR)
    assert case.registry_row()["avatar_url"] is None
    assert case.v1_group_list_avatar() is None
    assert case.v1_group_detail_avatar() is None
    assert case.v1_session_list_avatar() is None
    assert case.v1_session_detail_avatar() is None

    # Set again after the clear: the readbacks follow, and edits never grew
    # the BCS Bot table (an existing row is reused; new rows do not sneak in).
    rows_before, target_before = case.bcs_bot_count()
    case.put_avatar(_SECOND_URL)
    assert case.registry_row()["avatar_url"] == _SECOND_URL
    assert case.v1_group_list_avatar() == _SECOND_URL
    assert case.v1_group_detail_avatar() == _SECOND_URL
    assert case.v1_session_list_avatar() == _SECOND_URL
    assert case.v1_session_detail_avatar() == _SECOND_URL
    rows_after, target_after = case.bcs_bot_count()
    assert (rows_after, target_after) == (rows_before, target_before)


def test_repair_refuses_at_capability_gate_and_writes_nothing(repair_case):
    """A drifted, existing BCS target survives an owner repair byte-identically."""
    case = repair_case()
    case.admin_onboard(
        name=_DRIFTED_NAME,
        summary="fixture-drifted summary",
        avatar_url=_DRIFTED_AVATAR,
    )
    owner_before = case.read_bcs_owner()
    managers_before = case.read_bcs_managers()
    rows_before, target_before = case.bcs_bot_count()

    response = case.repair()

    case.assert_repair_refused_at_capability_gate(response)
    # No partial writes, no silent compensation: the drift the fixture made
    # through the official channel is exactly what the delivery side still
    # holds after the refused repair.
    assert case.read_bcs_owner() == owner_before
    assert case.read_bcs_managers() == managers_before
    assert case.registry_row() == {"name": _DRIFTED_NAME, "avatar_url": _DRIFTED_AVATAR}
    assert (case.bcs_bot_count()) == (rows_before, target_before)


def test_repair_missing_bcs_target_never_creates(repair_case):
    """A Backend Bot with no BCS counterpart is refused — and stays absent."""
    case = repair_case(register_bcs=False)
    case.assert_bcs_bot_absent()
    rows_before, target_before = case.bcs_bot_count()
    assert target_before == 0

    response = case.repair()

    case.assert_repair_refused_at_capability_gate(response)
    # Zero-creation, proven from the live store: the refusal at the capability
    # gate ran no registration/ensure path on the delivery side.
    assert case.bcs_bot_count() == (rows_before, 0)
    case.assert_bcs_bot_absent()


def test_repair_non_owner_never_reaches_the_interface(repair_case):
    """The owner-scoped admission refuses another user with the masked 404."""
    case = repair_case()
    rows_before = case.bcs_bot_count()

    response = case.repair(as_user=case.other_user)

    assert response.status_code == 404
    assert response.json()["data"] is None
    assert not any(
        "[COLLABORATION-REPAIR]" in line for line in case.log_lines()
    ), "a non-owner call must be refused before the repair module runs"
    assert case.bcs_bot_count() == rows_before
