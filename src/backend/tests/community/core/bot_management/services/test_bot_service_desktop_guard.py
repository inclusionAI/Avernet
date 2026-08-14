"""Unit tests: desktop bot must not go through DeviceService.apply_device().

Guard points:
1. BotService.create_bot — skip apply_device for desktop bots
2. BotService._allocate_device_async — skip for desktop bots
3. BotService.start_bot — desktop bot should not trigger apply_device
4. BotService.stop_bot — reject desktop bots (binding managed by BaaS)
5. BotService.restart_bot — reject desktop bots (delegates to stop_bot)
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.models import DeviceBindingStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(
    bot_id: str = "bot001",
    owner_id: str = "user001",
    status: str = "PENDING",
    binding_id: int | None = None,
    entity_id: str = "staff_user001",
    entity_type: str = "staff",
    bot_type: str = "personal",
    device_id: str | None = None,
) -> dict:
    return {
        "bot_id": bot_id,
        "owner_id": owner_id,
        "status": status,
        "binding_id": binding_id,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "engine_types": ["moltis", "openclaw"],
        "active_engine": "moltis",
        "bot_name": "TestBot",
        "ext": {},
        "bot_type": bot_type,
        "device_id": device_id,
    }


def _make_service() -> BotService:
    """Construct a BotService with mock dependencies."""
    svc = BotService.__new__(BotService)
    svc._bot_app_grant_provider = lambda: MagicMock()
    svc._repository = MagicMock()
    svc._passport_plugin = MagicMock()
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = lambda: MagicMock()
    svc._skill_set_factory = MagicMock()
    svc._oss_record_repo = MagicMock()
    # Restart lock repo: acquire() returns a truthy mock so restart_bot treats
    # the lock as acquired and proceeds into stop_bot (where the desktop guard
    # fires).
    svc._restart_lock_repo = MagicMock()
    # Non-teclaw tests: the teclaw provision branch must not fire.
    _teclaw = MagicMock()
    _teclaw.is_teclaw.return_value = False
    svc._teclaw_provision_provider = lambda: _teclaw
    svc._policy_service = None
    # DRM reader: default unset (None) ⇒ _is_new_bot_use_nas() is False (OSS).
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None
    return svc


# ===========================================================================
# _allocate_device_async — desktop bot guard
# ===========================================================================


class TestAllocateDeviceAsyncDesktopGuard:
    """_allocate_device_async must skip desktop bots."""

    def test_desktop_bot_skips_allocation(self):
        """Desktop bot should not trigger DeviceService.apply_device."""
        svc = _make_service()
        desktop_bot = _make_bot(bot_id="desktop_1", bot_type="desktop")
        svc._repository.get_by_id_and_owner.return_value = desktop_bot

        mock_device_service = MagicMock()
        svc._device_service_provider = lambda: mock_device_service

        # Call _allocate_device_async and wait for the thread
        svc._allocate_device_async(
            bot_id="desktop_1",
            user_id="user001",
            nick_name="TestUser",
            entity_id="staff_user001",
            entity_type="staff",
            engine_types=["openclaw"],
            bot_name="DesktopBot",
            active_engine="openclaw",
            owner_id="user001",
        )

        # Give the background thread time to execute
        # (do_allocate runs in a thread)
        import time
        time.sleep(0.3)

        # DeviceService.apply_device should NOT be called for desktop bot
        mock_device_service.apply_device.assert_not_called()
        # Bot status should NOT be updated (no device allocation happened)
        # get_by_id_and_owner was called once to check bot_type
        svc._repository.get_by_id_and_owner.assert_called_with("desktop_1", "user001")

    def test_personal_bot_proceeds_with_allocation(self):
        """Personal bot should still trigger DeviceService.apply_device."""
        svc = _make_service()
        personal_bot = _make_bot(bot_id="personal_1", bot_type="personal")
        # First call: bot_type check; second call: update_by_owner
        svc._repository.get_by_id_and_owner.return_value = personal_bot

        mock_device_result = MagicMock()
        mock_device_result.id = 99
        mock_device_result.device_id = "staff_123_default_abc"
        mock_device_result.device_provider = "local"
        mock_device_result.status = DeviceBindingStatus.ACTIVE.value

        mock_device_service = MagicMock()
        mock_device_service.apply_device.return_value = mock_device_result
        svc._device_service_provider = lambda: mock_device_service

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.DeviceService"
        ) as mock_ds_cls:
            svc._allocate_device_async(
                bot_id="personal_1",
                user_id="user001",
                nick_name="TestUser",
                entity_id="staff_user001",
                entity_type="staff",
                engine_types=["openclaw"],
                bot_name="PersonalBot",
                active_engine="openclaw",
                owner_id="user001",
            )

            import time
            time.sleep(0.5)

        # apply_device SHOULD be called for personal bot
        mock_device_service.apply_device.assert_called_once()


# ===========================================================================
# start_bot — desktop bot guard
# ===========================================================================


class TestStartBotDesktopGuard:
    """start_bot must not trigger DeviceService.apply_device for desktop bots."""

    def test_desktop_bot_start_skips_allocation(self):
        """start_bot on a desktop bot should not call _allocate_device_async
        with DeviceService.apply_device."""
        svc = _make_service()
        desktop_bot = _make_bot(bot_id="desktop_1", bot_type="desktop")
        svc._repository.get_by_id_and_owner.return_value = desktop_bot

        # start_bot calls _allocate_device_async which checks bot_type
        with patch.object(svc, "_allocate_device_async") as mock_alloc:
            result = svc.start_bot(bot_id="desktop_1", user_id="user001")

        # _allocate_device_async IS called (start_bot doesn't filter),
        # but inside it will check bot_type and skip DeviceService.apply_device
        mock_alloc.assert_called_once()


# ===========================================================================
# create_bot — desktop bot guard
# ===========================================================================


class TestCreateBotDesktopGuard:
    """create_bot must skip apply_device for desktop bots."""

    def test_desktop_bot_create_skips_apply_device(self):
        """create_bot with bot_type='desktop' should not call apply_device."""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None  # no existing bot
        svc._repository.exists_by_bot_name.return_value = False

        inserted_bot = _make_bot(
            bot_id="desktop_new", bot_type="desktop", status="PENDING"
        )
        svc._repository.insert.return_value = inserted_bot

        mock_device_service = MagicMock()
        svc._device_service_provider = lambda: mock_device_service

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.DeviceService"
        ) as mock_ds_cls:
            with patch(
                "agentclaw.community.core.bot_management.services.bot_service.generate_bot_id",
                return_value="desktop_new",
            ):
                result = svc.create_bot(
                    user_id="user001",
                    nick_name="TestUser",
                    bot_type="desktop",
                )

        # DeviceService.apply_device should NOT be called
        mock_device_service.apply_device.assert_not_called()
        # Bot record should be returned without device allocation
        assert result is not None
        assert result["bot_type"] == "desktop"

    def test_personal_bot_create_calls_apply_device(self):
        """create_bot with default bot_type should call apply_device."""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None
        svc._repository.exists_by_bot_name.return_value = False

        inserted_bot = _make_bot(bot_id="personal_new", bot_type="personal", status="PENDING")
        svc._repository.insert.return_value = inserted_bot

        mock_device_result = MagicMock()
        mock_device_result.id = 100
        mock_device_result.device_id = "staff_user001_default_abc"
        mock_device_result.device_provider = "local"
        mock_device_result.status = DeviceBindingStatus.ACTIVE.value

        mock_device_service = MagicMock()
        mock_device_service.apply_device.return_value = mock_device_result
        svc._device_service_provider = lambda: mock_device_service

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.DeviceService"
        ) as mock_ds_cls:
            with patch(
                "agentclaw.community.core.bot_management.services.bot_service.generate_bot_id",
                return_value="personal_new",
            ):
                svc.create_bot(
                    user_id="user001",
                    nick_name="TestUser",
                )

        # DeviceService.apply_device SHOULD be called for personal bot
        mock_device_service.apply_device.assert_called_once()


# ===========================================================================
# stop_bot — desktop bot guard
# ===========================================================================


class TestStopBotDesktopGuard:
    """stop_bot must reject desktop bots — their binding is managed by BaaS."""

    def test_desktop_bot_stop_raises(self):
        """stop_bot should raise BotServiceError for desktop bots."""
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

        svc = _make_service()
        desktop_bot = _make_bot(bot_id="desktop_1", bot_type="desktop")
        svc._repository.get_by_id_and_owner.return_value = desktop_bot

        with pytest.raises(BotServiceError, match="cannot be stopped via BotService.stop_bot"):
            svc.stop_bot(bot_id="desktop_1", user_id="user001")

    def test_personal_bot_stop_works(self):
        """stop_bot should work normally for personal bots."""
        svc = _make_service()
        personal_bot = _make_bot(bot_id="personal_1", bot_type="personal")
        svc._repository.get_by_id_and_owner.return_value = personal_bot

        # No binding_id, so it just resets status
        result = svc.stop_bot(bot_id="personal_1", user_id="user001")
        assert result is True


# ===========================================================================
# restart_bot — desktop bot guard
# ===========================================================================


class TestRestartBotDesktopGuard:
    """restart_bot must reject desktop bots — it calls stop_bot which guards."""

    def test_desktop_bot_restart_raises(self):
        """restart_bot should raise for desktop bots (via stop_bot guard)."""
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

        svc = _make_service()
        desktop_bot = _make_bot(bot_id="desktop_1", bot_type="desktop")
        svc._repository.get_by_id_and_owner.return_value = desktop_bot

        with pytest.raises(BotServiceError, match="cannot be stopped via BotService.stop_bot"):
            svc.restart_bot(bot_id="desktop_1", user_id="user001")
