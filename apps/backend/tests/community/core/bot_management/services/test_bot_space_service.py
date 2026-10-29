"""Policy tests for changing an owned Bot's Business Space."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    DisabledBotManagerSync,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaExceededError,
    BotQuotaScope,
    BotQuotaSnapshot,
)
from agentclaw.community.core.bot_management.services.bot_space_service import (
    BotSpaceService,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType
from agentclaw.community.utils.env_utils import get_current_env

pytestmark = pytest.mark.unit


def _space(
    *,
    space_id: int = 42,
    space_type: SpaceType = SpaceType.TEAM,
    personal_owner_id: str | None = None,
) -> SpaceRecord:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return SpaceRecord(
        id=space_id,
        space_code=f"spc-{space_id}",
        space_type=space_type,
        name="Team" if space_type is SpaceType.TEAM else "Personal",
        personal_owner_id=personal_owner_id,
        env="dev",
        created_by=personal_owner_id or "u1",
        updated_by=personal_owner_id or "u1",
        gmt_created=now,
        gmt_modified=now,
    )


def _service(*, bot: dict | None = None, space: SpaceRecord | None = None,
             manager_sync=None):
    repository = MagicMock()
    repository.get_by_id_and_owner.return_value = bot
    # A successful repository update returns the full Bot row, including its PK.
    repository.update_space_by_owner.return_value = (
        {"id": 101, **bot, "space_id": space.id}
        if bot is not None and space is not None
        else None
    )
    access = MagicMock()
    if space is not None:
        access.require_space_member.return_value = (space, MagicMock())
    quota = MagicMock()
    service = BotSpaceService(
        repository,
        access,
        quota,
        manager_sync if manager_sync is not None else DisabledBotManagerSync(),
    )
    return service, repository, access, quota


@pytest.mark.parametrize("space_type", [SpaceType.PERSONAL, SpaceType.TEAM])
def test_teclaw_transfer_skips_quota_not_membership(space_type):
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7,
           "active_engine": " TeClaw "}
    target = _space(space_type=space_type, personal_owner_id="u1")
    service, repository, access, quota = _service(bot=bot, space=target)
    quota.guard_add.side_effect = AssertionError("must not check quota")
    assert service.change_space(bot_id="b1", owner_id="u1", space_id=42).changed
    quota.guard_add.assert_not_called()
    access.require_space_member.assert_called_once_with(
        space_id=42, user_id="u1", env=get_current_env()
    )
    repository.update_space_by_owner.reset_mock()
    access.require_space_member.side_effect = SpaceAccessDeniedError("not a member")
    with pytest.raises(SpaceAccessDeniedError):
        service.change_space(bot_id="b1", owner_id="u1", space_id=42)
    repository.update_space_by_owner.assert_not_called()


def test_moves_owned_cloud_bot_to_joined_team_space():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    service, repository, access, quota = _service(bot=bot, space=target)

    result = service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    assert result.changed is True
    assert result.space == target
    assert result.bot["space_id"] == 42
    access.require_space_member.assert_called_once_with(
        space_id=42, user_id="u1", env=get_current_env()
    )
    repository.update_space_by_owner.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=42, env=get_current_env()
    )
    quota.guard_add.assert_called_once()


def test_moves_bot_back_to_owners_numeric_personal_space():
    bot = {"bot_id": "b1", "bot_type": "service", "space_id": 42}
    target = _space(
        space_id=8,
        space_type=SpaceType.PERSONAL,
        personal_owner_id="u1",
    )
    service, repository, _access, quota = _service(bot=bot, space=target)

    result = service.change_space(bot_id="b1", owner_id="u1", space_id=8)

    assert result.changed is True
    repository.update_space_by_owner.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=8, env=get_current_env()
    )
    quota.guard_add.assert_called_once()


def test_normalizing_legacy_personal_space_does_not_consume_new_capacity():
    bot = {"bot_id": "b1", "bot_type": "service", "space_id": None}
    target = _space(
        space_id=8,
        space_type=SpaceType.PERSONAL,
        personal_owner_id="u1",
    )
    service, repository, _access, quota = _service(bot=bot, space=target)
    quota.guard_add.side_effect = AssertionError("quota must not be consulted")

    result = service.change_space(bot_id="b1", owner_id="u1", space_id=8)

    assert result.changed is True
    repository.update_space_by_owner.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=8, env=get_current_env()
    )
    quota.guard_add.assert_not_called()


def test_missing_or_unowned_bot_is_masked_before_space_lookup():
    service, repository, access, quota = _service(bot=None, space=_space())

    with pytest.raises(BotNotFoundError):
        service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    repository.update_space_by_owner.assert_not_called()
    access.require_space_member.assert_not_called()
    quota.guard_add.assert_not_called()


@pytest.mark.parametrize(
    "error", [SpaceNotFoundError("missing"), SpaceAccessDeniedError("denied")]
)
def test_target_space_lookup_and_membership_failures_propagate(error):
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": None}
    service, repository, access, quota = _service(bot=bot)
    access.require_space_member.side_effect = error

    with pytest.raises(type(error)):
        service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    repository.update_space_by_owner.assert_not_called()
    quota.guard_add.assert_not_called()


def test_another_users_personal_space_is_rejected_even_with_bad_membership_row():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": None}
    target = _space(
        space_id=8,
        space_type=SpaceType.PERSONAL,
        personal_owner_id="u2",
    )
    service, repository, _access, quota = _service(bot=bot, space=target)

    with pytest.raises(SpaceAccessDeniedError):
        service.change_space(bot_id="b1", owner_id="u1", space_id=8)

    repository.update_space_by_owner.assert_not_called()
    quota.guard_add.assert_not_called()


def test_desktop_bot_cannot_move_to_team_space():
    bot = {"bot_id": "b1", "bot_type": "desktop", "space_id": None}
    service, repository, _access, quota = _service(bot=bot, space=_space())

    with pytest.raises(BotOperationNotAllowedError):
        service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    repository.update_space_by_owner.assert_not_called()
    quota.guard_add.assert_not_called()


def test_same_space_is_idempotent_and_skips_the_write():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 42}
    target = _space(space_id=42)
    service, repository, _access, quota = _service(bot=bot, space=target)

    result = service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    assert result.changed is False
    assert result.bot is bot
    repository.update_space_by_owner.assert_not_called()
    quota.guard_add.assert_not_called()


def test_bot_disappearing_during_write_is_masked_as_not_found():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    service, repository, _access, _quota = _service(bot=bot, space=_space())
    repository.update_space_by_owner.return_value = None

    with pytest.raises(BotNotFoundError):
        service.change_space(bot_id="b1", owner_id="u1", space_id=42)


def test_persistence_failure_propagates_instead_of_returning_success():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    service, repository, _access, _quota = _service(bot=bot, space=_space())
    repository.update_space_by_owner.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.change_space(bot_id="b1", owner_id="u1", space_id=42)


def test_full_target_space_blocks_the_move_before_the_write():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    service, repository, _access, quota = _service(bot=bot, space=target)
    scope = BotQuotaScope(
        owner_id="u1",
        space_id=target.id,
        space_name=target.name,
        space_type=target.space_type,
    )
    quota.guard_add.side_effect = BotQuotaExceededError(
        BotQuotaSnapshot(scope=scope, ceiling=20, used=20)
    )

    with pytest.raises(BotQuotaExceededError):
        service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    repository.update_space_by_owner.assert_not_called()
    quota.guard_add.assert_called_once_with(
        owner_id="u1", space_id=42, env=get_current_env()
    )


def test_explicit_env_threaded_to_every_collaborator():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    service, repository, access, quota = _service(bot=bot, space=target)

    service.change_space(bot_id="b1", owner_id="u1", space_id=42, env="prod")

    repository.get_by_id_and_owner.assert_called_once_with("b1", "u1", env="prod")
    access.require_space_member.assert_called_once_with(
        space_id=42, user_id="u1", env="prod"
    )
    quota.guard_add.assert_called_once_with(owner_id="u1", space_id=42, env="prod")
    repository.update_space_by_owner.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=42, env="prod"
    )


def test_default_env_falls_back_to_runtime_everywhere():
    from agentclaw.community.utils.env_utils import get_current_env

    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    service, repository, access, quota = _service(bot=bot, space=target)

    service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    access.require_space_member.assert_called_once_with(
        space_id=42, user_id="u1", env=get_current_env()
    )
    repository.get_by_id_and_owner.assert_called_once_with("b1", "u1", env=get_current_env())
    repository.update_space_by_owner.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=42, env=get_current_env()
    )


def test_changed_assignment_submits_bot_space_changed_sync():
    from agentclaw.community.kernel.bot_manager_sync import SyncReason
    from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

    bot = {"id": 733, "bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    manager_sync = MagicMock()
    service, repository, _, _ = _service(bot=bot, space=target, manager_sync=manager_sync)

    result = service.change_space(bot_id="b1", owner_id="u1", space_id=42, env="prod")

    assert result.bot is repository.update_space_by_owner.return_value
    assert result.bot["id"] == 733
    manager_sync.request_sync.assert_called_once_with(
        bot_pk=result.bot["id"],
        env="prod",
        avernet_tenant=get_current_avernet_tenant(),
        reason=SyncReason.BOT_SPACE_CHANGED,
    )


def test_same_space_assignment_submits_nothing():
    bot = {"id": 101, "bot_id": "b1", "bot_type": "personal", "space_id": 42}
    target = _space(space_id=42)
    manager_sync = MagicMock()
    service, _, _, _ = _service(bot=bot, space=target, manager_sync=manager_sync)

    result = service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    assert result.changed is False
    manager_sync.request_sync.assert_not_called()


def test_sync_submit_failure_leaves_the_move_successful():
    bot = {"id": 101, "bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    manager_sync = MagicMock()
    manager_sync.request_sync.side_effect = RuntimeError("queue closed")
    service, _, _, _ = _service(bot=bot, space=target, manager_sync=manager_sync)

    result = service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    assert result.changed is True
    assert result.bot["space_id"] == 42
    manager_sync.request_sync.assert_called_once()
