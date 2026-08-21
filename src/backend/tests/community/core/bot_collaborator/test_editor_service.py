"""Focused policy tests for the public bot-first Editors service methods."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.errors import (
    CannotRemoveSelfError,
    CollaboratorNotFoundError,
    CollaboratorSpaceMembershipError,
    InvalidCollaboratorRoleError,
    PermissionDeniedError,
)
from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
    PermissionLevel,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceType


OWNER = "owner-1"
ADMIN = "admin-1"
MEMBER = "member-1"


def _bot(**overrides):
    return {
        "id": 10,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "bot_type": "service",
        "space_id": None,
        **overrides,
    }


def _record(**overrides) -> CollaboratorRecord:
    now = datetime(2026, 8, 19, 10, 0, 0)
    values = {
        "id": 7,
        "bot_pk": 10,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "user_id": MEMBER,
        "role": CollaboratorRole.MEMBER,
        "operator_id": OWNER,
        "env": "dev",
        "gmt_create": now,
        "gmt_modified": now,
        **overrides,
    }
    return CollaboratorRecord(**values)


@pytest.fixture
def dependencies():
    collaborator_repo = Mock()
    collaborator_repo.get_by_bot_and_user.return_value = None
    bot_repo = Mock()
    bot_repo.get_by_id_and_owner.return_value = _bot()
    space_access = Mock()
    service = CollaboratorService(
        collaborator_repo=collaborator_repo,
        bot_repo=bot_repo,
        passport_plugin=Mock(),
        credentials_admins_writer=Mock(),
        space_access_service=space_access,
    )
    service.on_collaboration_changed = Mock()
    return service, collaborator_repo, bot_repo, space_access


def test_add_editor_rejects_an_unknown_role(dependencies):
    service, collaborator_repo, _, _ = dependencies

    with pytest.raises(InvalidCollaboratorRoleError):
        service.add_editor("bot-1", OWNER, MEMBER, OWNER, role="super-admin", env="dev")

    collaborator_repo.insert.assert_not_called()


def test_add_editor_requires_live_team_space_membership(dependencies):
    service, collaborator_repo, bot_repo, space_access = dependencies
    bot_repo.get_by_id_and_owner.return_value = _bot(space_id="spc-team")
    space_access.require_space_reference.return_value = Mock(
        id=22, space_type=SpaceType.TEAM
    )
    space_access.require_space_member.side_effect = SpaceAccessDeniedError(
        "membership required"
    )

    with pytest.raises(CollaboratorSpaceMembershipError):
        service.add_editor("bot-1", OWNER, MEMBER, OWNER, env="dev")

    collaborator_repo.insert.assert_not_called()


def test_unsupported_bot_is_masked_until_after_actor_authorization(dependencies):
    service, collaborator_repo, bot_repo, _ = dependencies
    bot_repo.get_by_id_and_owner.return_value = _bot(bot_type="personal")
    collaborator_repo.get_user_role.return_value = None

    with pytest.raises(PermissionDeniedError):
        service.list_editors("bot-1", OWNER, "stranger", env="dev")


@pytest.mark.parametrize(
    "record_override",
    [
        {"bot_pk": 999},
        {"bot_id": "other-bot"},
        {"owner_id": "other-owner"},
        {"env": "pre"},
    ],
)
def test_update_editor_masks_cross_scope_record_ids(dependencies, record_override):
    service, collaborator_repo, _, _ = dependencies
    collaborator_repo.get_by_id.return_value = _record(**record_override)

    with pytest.raises(CollaboratorNotFoundError):
        service.update_editor(
            "bot-1", OWNER, 7, OWNER, CollaboratorRole.ADMIN, env="dev"
        )

    collaborator_repo.update.assert_not_called()


def test_update_editor_changes_only_role_and_operator(dependencies):
    service, collaborator_repo, _, _ = dependencies
    collaborator_repo.get_user_role.return_value = CollaboratorRole.ADMIN
    record = _record()
    updated = _record(role=CollaboratorRole.ADMIN, operator_id=ADMIN)
    collaborator_repo.get_by_id.return_value = record
    collaborator_repo.update.return_value = updated

    result = service.update_editor(
        "bot-1", OWNER, 7, ADMIN, CollaboratorRole.ADMIN, env="dev"
    )

    assert result is updated
    collaborator_repo.update.assert_called_once_with(
        7, {"operator_id": ADMIN, "role": CollaboratorRole.ADMIN.value}
    )
    service.on_collaboration_changed.assert_called_once_with("bot-1", OWNER, "dev")


def test_non_owner_admin_must_leave_instead_of_removing_self(dependencies):
    service, collaborator_repo, _, _ = dependencies
    collaborator_repo.get_user_role.return_value = CollaboratorRole.ADMIN
    collaborator_repo.get_by_id.return_value = _record(user_id=ADMIN)

    with pytest.raises(CannotRemoveSelfError):
        service.remove_editor("bot-1", OWNER, 7, ADMIN, env="dev")

    collaborator_repo.delete.assert_not_called()


def test_removed_team_editor_has_no_operable_permission(dependencies):
    service, collaborator_repo, _, space_access = dependencies
    bot = _bot(space_id="22")
    collaborator_repo.get_user_role.return_value = CollaboratorRole.ADMIN
    space_access.require_space_reference.return_value = Mock(
        id=22, space_type=SpaceType.TEAM
    )
    space_access.require_space_member.side_effect = SpaceAccessDeniedError(
        "membership required"
    )

    level = service.get_operable_permission_level(
        bot=bot, user_id=ADMIN, env="dev"
    )

    assert level is PermissionLevel.NONE


def test_removed_team_bot_owner_keeps_owner_permission(dependencies):
    service, collaborator_repo, _, space_access = dependencies

    level = service.get_operable_permission_level(
        bot=_bot(space_id="22"), user_id=OWNER, env="dev"
    )

    assert level is PermissionLevel.OWNER
    collaborator_repo.get_user_role.assert_not_called()
    space_access.require_space_reference.assert_not_called()


def test_personal_space_editor_keeps_editor_permission(dependencies):
    service, collaborator_repo, _, space_access = dependencies
    collaborator_repo.get_user_role.return_value = CollaboratorRole.MEMBER

    level = service.get_operable_permission_level(
        bot=_bot(space_id=None), user_id=MEMBER, env="dev"
    )

    assert level is PermissionLevel.MEMBER
    space_access.require_space_reference.assert_not_called()


def test_batch_operable_permissions_reads_roles_once_and_caches_space_membership(
    dependencies,
):
    service, collaborator_repo, _, space_access = dependencies
    collaborator_repo.list_by_user.return_value = [
        _record(bot_pk=11, bot_id="bot-2", user_id=ADMIN),
        _record(bot_pk=12, bot_id="bot-3", user_id=ADMIN),
    ]
    space_access.require_space_reference.return_value = Mock(
        id=22, space_type=SpaceType.TEAM
    )

    levels = service.get_operable_permission_levels(
        bots=[
            _bot(id=10, bot_id="bot-1", owner_id=ADMIN, space_id="22"),
            _bot(id=11, bot_id="bot-2", space_id="22"),
            _bot(id=12, bot_id="bot-3", space_id="22"),
        ],
        user_id=ADMIN,
        env="dev",
    )

    assert levels == {
        10: PermissionLevel.OWNER,
        11: PermissionLevel.MEMBER,
        12: PermissionLevel.MEMBER,
    }
    collaborator_repo.list_by_user.assert_called_once_with(ADMIN, "dev")
    collaborator_repo.get_user_role.assert_not_called()
    space_access.require_space_reference.assert_called_once_with(space_ref="22")
    space_access.require_space_member.assert_called_once_with(
        space_id=22, user_id=ADMIN
    )
