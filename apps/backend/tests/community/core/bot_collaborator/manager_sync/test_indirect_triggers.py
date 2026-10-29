"""Indirect manager-sync triggers: Owner, Space membership, Bot space change.

Three business surfaces reach the BCS manager projections through the same
``BotManagerSyncProtocol`` side-channel Task 7 introduced — without owning any
dispatch logic:

- ``SpaceMemberService`` add/remove (the WorkOrder-notified member paths),
- ``SpaceManagementService`` bulk member ops and whole-Space delete,
- ``BotSpaceService.change_space`` (``BOT_SPACE_CHANGED`` only when the
  assignment really changed).

The Owner-handoff finish trigger lives with its journal fixture in
``test_owner_handoff.py``. These tests pin what each trigger must submit —
small refs derived from the Space the operation already resolved, the
actually-affected users of THIS operation, the explicit env threaded through
the business call — and that a failing submit never changes a committed
business result. Dispatcher admission, priority and coalescing are Task 6's
and stay out of scope here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.api.work_order_service import WorkOrderServiceProtocol
from agentclaw.community.core.bot_management.services.bot_space_service import (
    BotSpaceService,
)
from agentclaw.community.core.spaces.management_models import (
    MemberBatch,
    SetMemberRoles,
)
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceRecord,
    SpaceRole,
    SpaceType,
)
from agentclaw.community.core.spaces.services.space_management_service import (
    SpaceManagementService,
)
from agentclaw.community.core.spaces.services.space_member_service import (
    SpaceMemberService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncKey,
    SyncReason,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileInfo,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

#: Outside any request scope every test runs on the default tenant.
TENANT = get_current_avernet_tenant()

#: ``get_current_env()``'s answer in the unit column.
ENV = "dev"


class RecordingNotifier:
    """``BotManagerSyncProtocol`` double recording every admission.

    ``request_sync`` / ``request_space_sync`` are mocks, so tests keep the
    plain mock idioms — ``side_effect`` to make the notifier fail,
    ``assert_called_once`` for admission counts — while ``calls`` /
    ``changes`` record the exact submissions the business code sent.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[SyncKey, SyncReason]] = []
        self.changes: list[SpaceChange] = []
        spy = self

        def _record_sync(*, bot_pk, env, avernet_tenant, reason):
            spy.calls.append(
                (
                    SyncKey(
                        avernet_tenant=avernet_tenant, env=env, bot_pk=bot_pk
                    ),
                    reason,
                )
            )
            return SubmissionResult.QUEUED

        def _record_space(change: SpaceChange):
            spy.changes.append(change)
            return SubmissionResult.QUEUED

        self.request_sync = MagicMock(side_effect=_record_sync)
        self.request_space_sync = MagicMock(side_effect=_record_space)


def _space_record(
    *, space_id: int = 7, code: str = "spc-7", env: str = ENV
) -> SpaceRecord:
    now = datetime(2026, 10, 27, 10, 0, 0)
    return SpaceRecord(
        id=space_id,
        space_code=code,
        space_type=SpaceType.TEAM,
        name="Team",
        personal_owner_id=None,
        env=env,
        created_by="owner-1",
        updated_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


def _member_record(*, user_id: str = "member-1") -> SpaceMemberRecord:
    now = datetime(2026, 10, 27, 10, 0, 0)
    return SpaceMemberRecord(
        id=2,
        space_id=7,
        user_id=user_id,
        user_name=None,
        role=SpaceRole.MEMBER,
        env=ENV,
        created_by="owner-1",
        gmt_created=now,
        gmt_modified=now,
    )


def _space_member_case():
    """The ``test_space_member_service`` constructor arrangement plus the
    notifier this feature adds — same doubles, no new collaborators."""
    space = _space_record()
    repository = MagicMock()
    repository.get_member.return_value = None
    access = MagicMock()
    access.require_space_owner.return_value = (space, MagicMock())
    work_orders = MagicMock(spec=WorkOrderServiceProtocol)
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="member-1", nick_name=None
    )
    notifier = RecordingNotifier()
    service = SpaceMemberService(
        repository, access, staff_dept, work_orders, notifier
    )
    return service, repository, notifier, work_orders


def _management_case(*, env: str = "pre", space: SpaceRecord | None = None):
    repository = MagicMock()
    spaces = MagicMock()
    spaces.get_space.return_value = space
    staff = MagicMock()
    skill_center = MagicMock()
    notifier = RecordingNotifier()
    service = SpaceManagementService(
        repository,
        spaces,
        skill_center,
        staff,
        lambda: env,
        notifier,
    )
    return service, repository, spaces, notifier


def _bot_space_case(*, bot: dict, space: SpaceRecord):
    repository = MagicMock()
    repository.get_by_id_and_owner.return_value = bot
    repository.update_space_by_owner.return_value = {**bot, "space_id": space.id}
    access = MagicMock()
    access.require_space_member.return_value = (space, MagicMock())
    quota = MagicMock()
    notifier = RecordingNotifier()
    service = BotSpaceService(repository, access, quota, notifier)
    return service, repository, notifier


@pytest.fixture
def space_case():
    service, repository, notifier, _ = _space_member_case()
    return service, repository, notifier


def test_space_removal_returns_success_when_sync_submit_fails(space_case):
    service, repository, notifier = space_case
    repository.delete_member.return_value = True
    notifier.request_space_sync.side_effect = RuntimeError("queue closed")
    assert service.delete_member(space_id=22, actor_id="owner", user_id="member") is True
    repository.delete_member.assert_called_once()


def test_space_removal_hint_precedes_the_work_order_notification():
    service, repository, notifier, work_orders = _space_member_case()
    repository.delete_member.return_value = True
    order: list[str] = []
    notifier.request_space_sync.side_effect = lambda change: order.append("sync")
    work_orders.create_work_order_event.side_effect = lambda **_: order.append(
        "work_order"
    )

    assert service.delete_member(
        space_id=7, actor_id="owner-1", user_id="member-1"
    )
    assert order == ["sync", "work_order"]


def test_space_member_add_and_remove_carry_resolved_space_refs():
    service, repository, notifier, _ = _space_member_case()
    repository.add_member.return_value = _member_record(user_id="owner-2")
    repository.delete_member.return_value = True

    service.add_member(
        space_id=7, actor_id="owner-1", user_id="owner-2", role=SpaceRole.MEMBER
    )
    service.delete_member(space_id=7, actor_id="owner-1", user_id="owner-2")

    assert len(notifier.changes) == 2
    for change in notifier.changes:
        assert change == SpaceChange(
            avernet_tenant=TENANT,
            env=ENV,
            space_refs=("7", "team:7", "spc-7"),
            user_ids=("owner-2",),
        )


def test_member_rejoin_submits_a_second_hint_with_the_same_user():
    service, repository, notifier, _ = _space_member_case()
    repository.add_member.return_value = _member_record()
    repository.delete_member.return_value = True

    service.delete_member(space_id=7, actor_id="owner-1", user_id="member-1")
    service.add_member(
        space_id=7, actor_id="owner-1", user_id="member-1", role=SpaceRole.MEMBER
    )

    assert [change.user_ids for change in notifier.changes] == [
        ("member-1",),
        ("member-1",),
    ]


def test_role_update_keeps_membership_and_stays_silent():
    service, repository, notifier, _ = _space_member_case()
    repository.update_member_role.return_value = _member_record()

    service.update_role(
        space_id=7,
        actor_id="owner-1",
        user_id="member-1",
        role=SpaceRole.ADMIN,
    )

    notifier.request_space_sync.assert_not_called()


def test_add_sync_submit_failure_does_not_disturb_member_add():
    service, repository, notifier, _ = _space_member_case()
    repository.add_member.return_value = _member_record(user_id="owner-2")
    notifier.request_space_sync.side_effect = RuntimeError("queue closed")

    record = service.add_member(
        space_id=7, actor_id="owner-1", user_id="owner-2", role=SpaceRole.MEMBER
    )

    assert record.user_id == "owner-2"
    notifier.request_space_sync.assert_called_once()


def test_management_add_members_notify_only_actually_affected_users():
    service, repository, _, notifier = _management_case()
    repository.get_team.return_value = MagicMock()
    repository.add_members.return_value = {
        "space_id": 7,
        "added_user_ids": ["u1"],
        "existing_user_ids": ["u2"],
        "reactivated_user_ids": ["u3"],
    }

    service.add_members(7, MemberBatch(member_user_ids=["u1", "u2", "u3"]), "op")

    assert notifier.changes == [
        SpaceChange(
            avernet_tenant=TENANT,
            env="pre",
            space_refs=("7", "team:7"),
            user_ids=("u1", "u3"),
        )
    ]


def test_management_remove_members_notify_only_removed_users():
    service, repository, _, notifier = _management_case()
    repository.remove_members.return_value = {
        "space_id": 7,
        "removed_user_ids": ["u1"],
        "missing_user_ids": ["u2"],
    }

    service.remove_members(7, MemberBatch(member_user_ids=["u1", "u2"]))

    assert notifier.changes == [
        SpaceChange(
            avernet_tenant=TENANT,
            env="pre",
            space_refs=("7", "team:7"),
            user_ids=("u1",),
        )
    ]


def test_management_bulk_no_op_stays_silent():
    service, repository, _, notifier = _management_case()
    repository.add_members.return_value = {
        "space_id": 7,
        "added_user_ids": [],
        "existing_user_ids": ["u2"],
        "reactivated_user_ids": [],
    }
    repository.remove_members.return_value = {
        "space_id": 7,
        "removed_user_ids": [],
        "missing_user_ids": ["u1"],
    }

    service.add_members(7, MemberBatch(member_user_ids=["u2"]), "op")
    service.remove_members(7, MemberBatch(member_user_ids=["u1"]))

    notifier.request_space_sync.assert_not_called()


def test_management_role_change_on_active_members_does_not_notify():
    service, repository, _, notifier = _management_case()
    repository.set_member_roles.return_value = {
        "space_id": 7,
        "updated_user_ids": ["u1"],
        "missing_user_ids": [],
    }

    service.set_member_roles(7, SetMemberRoles(member_user_ids=["u1"], role="ADMIN"))

    notifier.request_space_sync.assert_not_called()


def test_space_delete_captures_refs_before_the_delete():
    team_space = _space_record(space_id=19, code="spc-19", env="pre")
    service, repository, spaces, notifier = _management_case(space=team_space)
    order: list[str] = []

    def _read_then(**_: object):
        order.append("read")
        return team_space

    def _delete_then(**_: object):
        order.append("delete")
        return {"space_id": 19, "deleted": True, "removed_member_count": 3}

    spaces.get_space.side_effect = _read_then
    repository.delete_team.side_effect = _delete_then

    result = service.delete_team_with_members(19)

    assert result["deleted"] is True
    assert order == ["read", "delete"]
    assert notifier.changes == [
        SpaceChange(
            avernet_tenant=TENANT,
            env="pre",
            space_refs=("19", "team:19", "spc-19"),
            user_ids=(),
        )
    ]


def test_space_delete_without_team_row_still_carries_numeric_refs():
    service, repository, spaces, notifier = _management_case()
    spaces.get_space.return_value = None
    repository.delete_team.return_value = {
        "space_id": 21,
        "deleted": True,
        "removed_member_count": 0,
    }

    service.delete_empty_team(21)

    assert notifier.changes == [
        SpaceChange(
            avernet_tenant=TENANT,
            env="pre",
            space_refs=("21", "team:21"),
            user_ids=(),
        )
    ]


def test_space_delete_submit_failure_keeps_the_delete_result():
    service, repository, _, notifier = _management_case(
        space=_space_record(space_id=19, code="spc-19", env="pre")
    )
    repository.delete_team.return_value = {
        "space_id": 19,
        "deleted": True,
        "removed_member_count": 3,
    }
    notifier.request_space_sync.side_effect = RuntimeError("queue closed")

    result = service.delete_team_with_members(19)

    assert result["deleted"] is True
    notifier.request_space_sync.assert_called_once()


def _moving_bot(space_id: int = 7) -> dict:
    return {"id": 101, "bot_id": "b1", "bot_type": "personal", "space_id": space_id}


def _target_space() -> SpaceRecord:
    return _space_record(space_id=42, code="spc-42", env="prod")


def test_bot_space_change_submits_explicit_env_and_returned_bot_pk():
    service, repository, notifier = _bot_space_case(
        bot=_moving_bot(space_id=7), space=_target_space()
    )
    repository.update_space_by_owner.return_value = {
        **_moving_bot(space_id=7),
        "space_id": 42,
    }

    service.change_space(bot_id="b1", owner_id="u1", space_id=42, env="prod")

    assert notifier.calls == [
        (
            SyncKey(avernet_tenant=TENANT, env="prod", bot_pk=101),
            SyncReason.BOT_SPACE_CHANGED,
        )
    ]


def test_bot_space_change_uses_runtime_env_only_as_its_fallback():
    service, _, notifier = _bot_space_case(
        bot={"id": 202, "bot_id": "b2", "bot_type": "personal", "space_id": 7},
        space=_target_space(),
    )

    service.change_space(bot_id="b2", owner_id="u1", space_id=42)

    assert notifier.calls == [
        (
            SyncKey(avernet_tenant=TENANT, env=ENV, bot_pk=202),
            SyncReason.BOT_SPACE_CHANGED,
        )
    ]


def test_bot_space_change_with_no_assignment_change_stays_silent():
    service, _, notifier = _bot_space_case(
        bot={"id": 303, "bot_id": "b3", "bot_type": "personal", "space_id": 42},
        space=_target_space(),
    )

    result = service.change_space(bot_id="b3", owner_id="u1", space_id=42)

    assert result.changed is False
    notifier.request_sync.assert_not_called()


def test_bot_space_change_survives_sync_submit_failure(caplog):
    service, _, notifier = _bot_space_case(
        bot=_moving_bot(space_id=7), space=_target_space()
    )
    notifier.request_sync.side_effect = RuntimeError("queue closed")

    with caplog.at_level(logging.ERROR, logger="start"):
        result = service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    assert result.changed is True
    assert result.bot["space_id"] == 42
    notifier.request_sync.assert_called_once()
    failure = next(
        record
        for record in caplog.records
        if record.getMessage() == "bot manager sync notification failed"
    )
    assert failure.bot_pk == 101
