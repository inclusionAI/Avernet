"""Unit tests for ``BotService._list_bot_members``.

Covers the branches exercised when enriching appcoding-bots responses with a
bot's member (collaborator) list:

- ``owner_id`` missing -> ``[]`` (no repo call).
- happy path: collaborators are serialized to ``{user_id, user_name}`` only.
- repository failure -> degrades to ``[]`` without raising.

The service is constructed with MagicMock collaborators (matching the existing
``test_list_desktop_live_status`` construction contract); only the
``collaborator_repo`` seam is driven.
"""
from unittest.mock import MagicMock

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
)
from agentclaw.community.core.bot_management.services.bot_service import BotService


def _make_bot_service(collaborator_repo) -> BotService:
    return BotService(
        drm_reader=MagicMock(),
        repository=MagicMock(),
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
        collaborator_repo=collaborator_repo,
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )


def _record(user_id: str, user_name, role=CollaboratorRole.MEMBER) -> CollaboratorRecord:
    return CollaboratorRecord(
        bot_pk=1,
        bot_id="app_bot_1",
        owner_id="test_user",
        user_id=user_id,
        user_name=user_name,
        role=role,
        operator_id="test_user",
    )


class TestListBotMembers:
    def test_missing_owner_returns_empty(self):
        svc = _make_bot_service(collaborator_repo=MagicMock())
        # A truthy-falsy owner_id (None / empty) short-circuits before any repo call.
        assert svc._list_bot_members(bot_id="app_bot_1", owner_id=None) == []
        assert svc._list_bot_members(bot_id="app_bot_1", owner_id="") == []
        svc._collaborator_repo.list_by_bot.assert_not_called()

    def test_maps_collaborators_to_member_dicts(self):
        repo = MagicMock()
        repo.list_by_bot.return_value = [
            _record("u1", "Alice"),
            _record("u2", None),
        ]
        svc = _make_bot_service(collaborator_repo=repo)

        members = svc._list_bot_members(bot_id="app_bot_1", owner_id="test_user")

        repo.list_by_bot.assert_called_once()
        kwargs = repo.list_by_bot.call_args.kwargs
        assert kwargs["bot_id"] == "app_bot_1"
        assert kwargs["owner_id"] == "test_user"
        # env is read via get_current_env() at call time.
        assert "env" in kwargs

        assert members == [
            {"user_id": "u1", "user_name": "Alice"},
            {"user_id": "u2", "user_name": None},
        ]

    def test_no_collaborators_returns_empty(self):
        repo = MagicMock()
        repo.list_by_bot.return_value = []
        svc = _make_bot_service(collaborator_repo=repo)
        assert svc._list_bot_members(bot_id="app_bot_1", owner_id="test_user") == []

    def test_repository_failure_degrades_to_empty(self):
        repo = MagicMock()
        repo.list_by_bot.side_effect = RuntimeError("db down")
        svc = _make_bot_service(collaborator_repo=repo)
        # Enrichment must never break the coding-bots listing.
        assert svc._list_bot_members(bot_id="app_bot_1", owner_id="test_user") == []
