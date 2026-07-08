"""Unit tests for create_bot default bot soft-delete protection.

When bot_id == 'default', soft_delete_by_owner MUST NOT be called
during device allocation failure rollback.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
)
from agentclaw.community.core.devices.errors import (
    DeviceAllocateError,
    DeviceLimitExceededError,
    ResourceInsufficientError,
)


def _make_service() -> BotService:
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._repository.count_by_owner.return_value = 0
    svc._repository.get_by_id_and_owner.return_value = None
    svc._repository.create.return_value = {
        "id": 1,
        "bot_id": "default",
        "owner_id": "user001",
        "entity_id": "staff_user001",
        "entity_type": "staff",
        "bot_type": "personal",
        "template_type": "normalCC",
        "active_engine": "moltis",
        "status": "PENDING",
    }
    svc._repository.soft_delete_by_owner = MagicMock()
    svc._allocation_config = SimpleNamespace(
        mode="multi",
        max_devices_per_entity=5,
    )
    svc._passport_plugin = MagicMock()
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = lambda: MagicMock()
    _teclaw_provision = MagicMock()
    _teclaw_provision.is_teclaw.return_value = False
    svc._teclaw_provision_provider = lambda: _teclaw_provision
    svc._skill_set_factory = MagicMock()
    svc._oss_record_repo = MagicMock()
    svc._device_binding_repo = MagicMock()
    svc._cleanup_service = MagicMock()
    svc._bcn_service = MagicMock()
    svc._bot_publish_repo = MagicMock()
    svc._template_service = MagicMock()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="", aixcore_base_url_pre="")
    svc._policy_service = None
    return svc


def _patch_create_bot_dependencies():
    """Patch heavy dependencies inside create_bot to isolate device-allocation error path."""
    return patch.multiple(
        "agentclaw.community.core.bot_management.services.bot_service",
        _copy_tree_fast=MagicMock(),
    )


class TestDefaultBotSoftDeleteProtection:
    """When bot_id == 'default', soft_delete_by_owner must NOT be called on error."""

    @pytest.fixture
    def svc(self) -> BotService:
        return _make_service()

    @pytest.fixture
    def mock_device_service(self) -> MagicMock:
        ds = MagicMock()
        ds.apply_device.side_effect = ResourceInsufficientError("no resources")
        return ds

    def test_known_error_default_bot_no_soft_delete(self, svc, mock_device_service):
        """ResourceInsufficientError + bot_id='default' → no soft_delete_by_owner."""
        svc._device_service_provider = lambda: mock_device_service

        with _patch_create_bot_dependencies():
            with pytest.raises(BotServiceError, match="设备申请失败"):
                svc.create_bot(
                    user_id="user001",
                    nick_name="TestUser",
                    bot_id="default",
                )

        svc._repository.soft_delete_by_owner.assert_not_called()

    def test_unknown_error_default_bot_no_soft_delete(self, svc):
        """Generic Exception + bot_id='default' → no soft_delete_by_owner."""
        mock_device_service = MagicMock()
        mock_device_service.apply_device.side_effect = RuntimeError("unexpected")
        svc._device_service_provider = lambda: mock_device_service

        with _patch_create_bot_dependencies():
            with pytest.raises(BotServiceError, match="设备申请失败"):
                svc.create_bot(
                    user_id="user001",
                    nick_name="TestUser",
                    bot_id="default",
                )

        svc._repository.soft_delete_by_owner.assert_not_called()

    def test_known_error_non_default_bot_still_soft_deletes(self, svc):
        """ResourceInsufficientError + bot_id != 'default' → soft_delete_by_owner IS called."""
        mock_device_service = MagicMock()
        mock_device_service.apply_device.side_effect = DeviceLimitExceededError("limit exceeded")
        svc._device_service_provider = lambda: mock_device_service

        with _patch_create_bot_dependencies():
            with pytest.raises(BotServiceError, match="设备申请失败"):
                svc.create_bot(
                    user_id="user001",
                    nick_name="TestUser",
                    bot_id="my-custom-bot",
                )

        svc._repository.soft_delete_by_owner.assert_called_once_with("my-custom-bot", "user001")

    def test_unknown_error_non_default_bot_still_soft_deletes(self, svc):
        """Generic Exception + bot_id != 'default' → soft_delete_by_owner IS called."""
        mock_device_service = MagicMock()
        mock_device_service.apply_device.side_effect = RuntimeError("boom")
        svc._device_service_provider = lambda: mock_device_service

        with _patch_create_bot_dependencies():
            with pytest.raises(BotServiceError, match="设备申请失败"):
                svc.create_bot(
                    user_id="user001",
                    nick_name="TestUser",
                    bot_id="my-custom-bot",
                )

        svc._repository.soft_delete_by_owner.assert_called_once_with("my-custom-bot", "user001")
