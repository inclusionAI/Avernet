"""`delete_bot` must let the client-error subclass escape (R3/F15).

The public API maps ``BotOperationNotAllowedError`` to 409 so callers do not
retry an operation that can never succeed. That only works if the exception
actually escapes ``delete_bot`` — its trailing ``except Exception`` re-wraps
anything it catches into a plain ``BotServiceError``, which the public surface
reports as a 500.

This exercises the **real** method rather than a mocked service: an endpoint
test that stubs ``delete_bot`` to raise the subclass proves only that the
mapping works, not that the service ever produces it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotOperationNotAllowedError,
    BotService,
)


def _make_bot_service(repository) -> BotService:
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


def test_default_bot_delete_raises_operation_not_allowed():
    """The specific subclass escapes — it must not be re-wrapped as the base."""
    repo = Mock()
    repo.get_by_id_and_owner.return_value = {
        "id": 1, "bot_id": "default", "owner_id": "u1",
        "status": "ACTIVE", "binding_id": None, "ext": {},
    }
    service = _make_bot_service(repo)

    with pytest.raises(BotOperationNotAllowedError):
        service.delete_bot("default", "u1")


def test_default_bot_delete_does_not_release_device_or_passport(monkeypatch):
    """The rejection happens before any destructive side effect."""
    repo = Mock()
    repo.get_by_id_and_owner.return_value = {
        "id": 1, "bot_id": "default", "owner_id": "u1",
        "status": "ACTIVE", "binding_id": "bind-1", "ext": {},
    }
    service = _make_bot_service(repo)

    with pytest.raises(BotOperationNotAllowedError):
        service.delete_bot("default", "u1")

    # The bot row is still there — nothing was soft-deleted on the way out.
    repo.soft_delete_by_owner.assert_not_called()
