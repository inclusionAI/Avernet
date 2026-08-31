from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.core.service_bot.services.service_edit_lock_service import (
    ServiceEditLockService,
)


@pytest.fixture
def deps():
    values = SimpleNamespace(
        bot_repo=Mock(),
        collaborator_service=Mock(),
        lock_service=Mock(),
    )
    values.bot_repo.get_by_id_and_owner.return_value = {
        "id": 10,
        "bot_id": "bot-1",
        "owner_id": "owner",
        "bot_type": "service",
    }
    values.lock_service.get_lock_info.return_value = SimpleNamespace(
        lock=None,
        holder_name=None,
        has_collaborators=False,
        is_owner=False,
    )
    values.service = ServiceEditLockService(
        bot_repo=values.bot_repo,
        collaborator_service=values.collaborator_service,
        lock_service=values.lock_service,
    )
    return values


def test_get_lock_returns_service_projection_without_publication_dependencies(
    deps, monkeypatch
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services."
        "service_edit_lock_service.get_current_env",
        lambda: "pre",
    )
    deps.lock_service.get_lock_info.return_value = SimpleNamespace(
        lock=SimpleNamespace(holder_user_id="owner"),
        holder_name="Owner",
        has_collaborators=True,
        is_owner=True,
    )

    info = deps.service.get_lock("bot-1", actor_id="owner", owner_id="owner")

    assert info.need_lock is True
    assert info.holder_name == "Owner"
    deps.bot_repo.get_by_id_and_owner.assert_called_once_with("bot-1", "owner")
    deps.lock_service.get_lock_info.assert_called_once_with(
        "bot-1", "owner", "owner"
    )


def test_missing_bot_is_masked(deps):
    deps.bot_repo.get_by_id_and_owner.return_value = None

    with pytest.raises(ServicePublicationNotFoundError, match="bot not found"):
        deps.service.get_lock("missing", actor_id="owner", owner_id="owner")


def test_non_service_bot_is_rejected(deps):
    deps.bot_repo.get_by_id_and_owner.return_value["bot_type"] = "personal"

    with pytest.raises(ServicePublicationUnsupportedError):
        deps.service.get_lock("bot-1", actor_id="owner", owner_id="owner")


def test_non_member_is_masked(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services."
        "service_edit_lock_service.get_current_env",
        lambda: "pre",
    )
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.NONE

    with pytest.raises(ServicePublicationNotFoundError, match="bot not found"):
        deps.service.get_lock("bot-1", actor_id="stranger", owner_id="owner")


def test_acquire_is_a_noop_when_bot_has_no_collaborators(deps):
    assert (
        deps.service.acquire_lock("bot-1", actor_id="owner", owner_id="owner")
        is None
    )
    deps.lock_service.acquire_lock.assert_not_called()


def test_acquire_uses_the_resolved_bot_owner(deps):
    deps.lock_service.get_lock_info.return_value.has_collaborators = True
    deps.lock_service.acquire_lock.return_value = SimpleNamespace(
        holder_user_id="owner"
    )

    result = deps.service.acquire_lock(
        "bot-1", actor_id="owner", owner_id="owner"
    )

    assert result.holder_user_id == "owner"
    deps.lock_service.acquire_lock.assert_called_once_with(
        "bot-1", "owner", "owner"
    )


def test_release_does_not_force_another_users_lock(deps):
    deps.lock_service.release_lock.return_value = True

    assert deps.service.release_lock(
        "bot-1", actor_id="owner", owner_id="owner"
    )
    deps.lock_service.release_lock.assert_called_once_with(
        "bot-1", "owner", "owner", False
    )


def test_steal_uses_the_resolved_bot_owner(deps):
    deps.lock_service.get_lock_info.return_value.has_collaborators = True
    deps.lock_service.steal_lock.return_value = SimpleNamespace(
        holder_user_id="owner"
    )

    result = deps.service.steal_lock(
        "bot-1", actor_id="owner", owner_id="owner"
    )

    assert result.holder_user_id == "owner"
    deps.lock_service.steal_lock.assert_called_once_with(
        "bot-1", "owner", "owner"
    )
