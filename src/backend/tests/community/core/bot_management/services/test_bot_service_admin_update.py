"""Unit tests for BotService.admin_update_bot.

Covers all branches: success paths, validation errors, not found, name conflict.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_service():
    """Construct BotService with mock dependencies."""
    from agentclaw.community.core.bot_management.services.bot_service import BotService

    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._template_service = MagicMock()
    svc._device_binding_repo = None
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = lambda: MagicMock()
    return svc


class TestAdminUpdateBot:
    def test_missing_owner_id_raises(self):
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

        svc = _make_service()
        with pytest.raises(BotServiceError, match="owner_id is required"):
            svc.admin_update_bot(bot_id="bot1", owner_id="")

    def test_bot_not_found_raises(self):
        from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None

        with pytest.raises(BotNotFoundError):
            svc.admin_update_bot(bot_id="bot1", owner_id="owner1")

    def test_update_bot_name_success(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._repository.get_by_bot_name.return_value = None
        svc._repository.update_by_owner.return_value = {"bot_id": "bot1", "bot_name": "New Name"}
        svc._template_service.get_template_config.return_value = None

        # Mock get_bot for the response
        with patch.object(svc, "get_bot", return_value={"bot_id": "bot1", "bot_name": "New Name"}):
            result = svc.admin_update_bot(bot_id="bot1", owner_id="owner1", bot_name="New Name")

        assert result["bot_name"] == "New Name"
        svc._repository.update_by_owner.assert_called_once()

    def test_update_bot_name_conflict_raises(self):
        from agentclaw.community.core.bot_management.services.bot_service import BotNameExistsError

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._repository.get_by_bot_name.return_value = {"bot_id": "other_bot"}

        with pytest.raises(BotNameExistsError):
            svc.admin_update_bot(bot_id="bot1", owner_id="owner1", bot_name="Taken Name")

    def test_update_bot_desc_success(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._repository.update_by_owner.return_value = {"bot_id": "bot1", "bot_desc": "New Desc"}
        svc._template_service.get_template_config.return_value = None

        with patch.object(svc, "get_bot", return_value={"bot_id": "bot1", "bot_desc": "New Desc"}):
            result = svc.admin_update_bot(bot_id="bot1", owner_id="owner1", bot_desc="New Desc")

        assert result["bot_desc"] == "New Desc"

    def test_update_template_config_merge_existing(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._template_service.get_template_config.return_value = {"image": "old:v1", "envs": {"A": "1"}}
        svc._template_service.exists_template.return_value = True
        svc._template_service.update_template.return_value = {}

        with patch.object(svc, "get_bot", return_value={"bot_id": "bot1"}):
            result = svc.admin_update_bot(
                bot_id="bot1", owner_id="owner1",
                template_config={"image": "new:v2"},
            )

        # Should merge: old envs preserved, image overwritten
        call_args = svc._template_service.update_template.call_args
        merged = call_args[1]["template_config"]
        assert merged["image"] == "new:v2"
        assert merged["envs"] == {"A": "1"}
        assert "warning" in result

    def test_update_template_config_create_new(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._template_service.get_template_config.return_value = None
        svc._template_service.exists_template.return_value = False
        svc._template_service.create_template.return_value = {}

        with patch.object(svc, "get_bot", return_value={"bot_id": "bot1"}):
            result = svc.admin_update_bot(
                bot_id="bot1", owner_id="owner1",
                template_config={"image": "new:v1", "resource_spec": {"cpu": 4, "memory": 8, "disk": 50}},
            )

        svc._template_service.create_template.assert_called_once()
        assert "warning" in result

    def test_invalid_resource_spec_raises(self):
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._template_service.get_template_config.return_value = None
        svc._template_service.exists_template.return_value = False
        svc._template_service.create_template.return_value = {}

        with pytest.raises(BotServiceError, match="校验失败"):
            svc.admin_update_bot(
                bot_id="bot1", owner_id="owner1",
                template_config={"resource_spec": {"cpu": -1, "memory": 8}},
            )

    def test_no_updates_returns_current_bot(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._template_service.get_template_config.return_value = {"image": "existing:v1"}

        with patch.object(svc, "get_bot", return_value={"bot_id": "bot1"}):
            result = svc.admin_update_bot(bot_id="bot1", owner_id="owner1")

        svc._repository.update_by_owner.assert_not_called()
        assert result["template_config"] == {"image": "existing:v1"}
        assert "warning" not in result

    def test_update_after_owner_update_fails_raises(self):
        from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
        svc._repository.get_by_bot_name.return_value = None
        svc._repository.update_by_owner.return_value = None

        with pytest.raises(BotNotFoundError):
            svc.admin_update_bot(bot_id="bot1", owner_id="owner1", bot_name="New")
