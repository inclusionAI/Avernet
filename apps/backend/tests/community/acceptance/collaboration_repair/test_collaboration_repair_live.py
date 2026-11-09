"""Live avatar sync and genuine HTTP repair on the shared coverage stack.

Coverage-only DI supplies real ephemeral provider credentials; the runner
allowlists that provider in disposable BCS configuration. All repair reads,
projectors, ownership/managers delivery and metadata delivery remain production
implementations. Ordinary public-local startup remains credentialless and its
503 capability refusal stays covered by the composition/adapter unit suites.
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


def test_repair_restores_saved_profile_and_confirms_authority(repair_case):
    """An owner repairs drift through HTTP and reads actual BCS state back."""
    case = repair_case()
    name = f"Saved {case.suffix}"
    case.save_manager(case.other_user)
    case.save_caller(enabled=True, revision=1)
    assert case.read_bcs_managers() == set()
    case.save_profile(name=name, summary="saved repair summary", avatar_url=_SET_URL)
    case.admin_onboard(
        name=_DRIFTED_NAME,
        summary="fixture-drifted summary",
        avatar_url=_DRIFTED_AVATAR,
    )
    rows_before = case.bcs_bot_count()

    response = case.repair()

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["bot_id"] == case.bot_id
    assert report["status"] == "succeeded"
    assert [(item["component"], item["status"], item["error_code"])
            for item in report["items"]] == [
        ("ownership", "succeeded", None),
        ("profile", "succeeded", None),
        ("managers", "succeeded", None),
    ]
    assert case.read_bcs_owner() == case.owner
    assert case.read_bcs_managers() == {case.other_user}
    assert case.registry_row() == {"name": name, "avatar_url": _SET_URL}
    assert case.read_bcs_summary() == "saved repair summary"
    assert case.bcs_bot_count() == rows_before
    case.assert_no_repair_queue()


@pytest.mark.parametrize("summary", ["", " \t\n"])
def test_empty_summary_succeeds_while_preserving_existing_bcs_summary(repair_case, summary):
    case = repair_case()
    name = f"Saved {case.suffix}"
    case.save_profile(name=name, summary=summary, avatar_url="")
    case.admin_onboard(
        name=_DRIFTED_NAME, summary="old summary", avatar_url=_DRIFTED_AVATAR,
    )

    response = case.repair()

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["status"] == "succeeded"
    assert report["items"][1] == {
        "component": "profile", "status": "succeeded",
        "error_code": None,
    }
    assert report["items"][0]["status"] == "succeeded"
    assert report["items"][2]["status"] == "succeeded"
    assert case.registry_row() == {"name": name, "avatar_url": None}
    assert case.read_bcs_summary() == "old summary"
    case.assert_no_repair_queue()


def test_repair_missing_bcs_target_never_creates(repair_case):
    """A Backend Bot with no BCS counterpart is refused — and stays absent."""
    case = repair_case(register_bcs=False)
    case.assert_bcs_bot_absent()
    rows_before, target_before = case.bcs_bot_count()
    assert target_before == 0

    response = case.repair()

    assert response.status_code == 404
    assert response.json()["data"] is None
    # A real existence probe finds absence; repair never registers a Bot.
    assert case.bcs_bot_count() == (rows_before, 0)
    case.assert_bcs_bot_absent()
    case.assert_no_repair_queue()


def test_repair_unreadable_saved_avatar_refuses_without_clearing(repair_case):
    """Invalid persisted facts cannot clear BCS's confirmed profile avatar."""
    case = repair_case(register_avatar_url=_REGISTRATION_URL)
    before = case.registry_row()
    owner_before = case.read_bcs_owner()
    managers_before = case.read_bcs_managers()
    rows_before = case.bcs_bot_count()
    case.save_unreadable_avatar()

    response = case.repair()

    assert response.status_code == 409
    assert response.json()["data"] is None
    assert response.json()["message"] == "Bot profile facts could not be read"
    assert case.registry_row() == before
    assert case.read_bcs_owner() == owner_before
    assert case.read_bcs_managers() == managers_before
    assert case.bcs_bot_count() == rows_before
    case.assert_no_repair_queue()


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
