"""Renaming must not take another owner's name (R9/F40).

``bot_id`` is unique per owner, not globally — every owner's first bot is
``"default"``. The duplicate-name check identified "the current record" by
``bot_id`` alone, so another owner's ``default`` row compared equal to this one
and its name could be taken, even though create and check-name enforce the name
tenant-wide.

Drives the real ``update_bot`` rather than a mocked service: the defect is in
the comparison, so a test that stubs it out would prove nothing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNameExistsError,
    BotService,
)


def _service(repository) -> BotService:
    return BotService(
        drm_reader=MagicMock(),
        repository=repository,
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(
            is_teclaw=MagicMock(return_value=False)
        ),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )


def _repo(*, current_owner: str, existing_owner: str):
    repo = Mock()
    repo.get_by_id_and_owner.return_value = {
        "id": 1, "bot_id": "default", "owner_id": current_owner,
        "status": "ACTIVE", "ext": {},
    }
    # Another row already holds the requested name.
    repo.get_by_bot_name.return_value = {
        "id": 2, "bot_id": "default", "owner_id": existing_owner,
    }
    return repo


def test_cannot_take_another_owners_name():
    """Two `default` bots with different owners are different records."""
    service = _service(_repo(current_owner="u1", existing_owner="u2"))

    with pytest.raises(BotNameExistsError):
        service.update_bot("default", "u1", bot_name="Taken")


def test_renaming_own_bot_to_its_current_name_is_allowed():
    """The guard must still recognise the record being updated as itself."""
    repo = _repo(current_owner="u1", existing_owner="u1")
    service = _service(repo)

    service.update_bot("default", "u1", bot_name="Mine")

    # Not rejected — the update reached persistence.
    assert repo.update_by_owner.called or repo.update.called, (
        "expected the rename to be persisted"
    )
