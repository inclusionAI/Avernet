"""Unit tests for BotService.stop_bot / start_bot / restart_bot.

Phase 1 功能：restart_bot 拆分为 stop_bot + start_bot 两个独立方法。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
from agentclaw.community.core.devices.models import DeviceBindingStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(
    bot_id: str = "bot001",
    owner_id: str = "user001",
    status: str = "ACTIVE",
    binding_id: int | None = 42,
    entity_id: str = "staff_user001",
    entity_type: str = "staff",
    ext: dict | None = None,
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
        "ext": ext,
    }


def _make_binding(status: str = "ACTIVE"):
    binding = MagicMock()
    binding.status = status
    return binding


def _make_service() -> BotService:
    """构造带 mock repository 的 BotService。"""
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._passport_plugin = MagicMock()
    # Cycle-breaker providers installed by __init__; tests using __new__
    # override ``svc._device_service_provider`` when they need to assert on
    # the resolved DeviceService.
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = lambda: MagicMock()
    # Restart idempotency lock repo. Default: acquire() returns a truthy mock
    # so restart_bot treats the lock as acquired and proceeds to stop+start.
    svc._restart_lock_repo = MagicMock()
    # DRM reader: default "unset" (None) so BCN-register reads as disabled;
    # tests that exercise the flag override ``_drm_reader.read.return_value``.
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None
    return svc


# ===========================================================================
# stop_bot 测试
# ===========================================================================


class TestStopBot:
    """BotService.stop_bot()"""

    def test_raises_when_user_id_missing(self):
        """user_id 为空时抛出 BotServiceError。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

        svc = _make_service()
        with pytest.raises(BotServiceError, match="User ID is required"):
            svc.stop_bot(bot_id="bot001", user_id="")

    def test_raises_when_bot_not_found(self):
        """bot 不存在时抛出 BotNotFoundError。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None

        with pytest.raises(BotNotFoundError):
            svc.stop_bot(bot_id="bot001", user_id="user001")

    def test_releases_device_and_resets_status(self):
        """正常流程：释放设备 + 重置 bot 状态为 PENDING。"""
        svc = _make_service()
        bot = _make_bot()
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_device_service = MagicMock()
        mock_binding = _make_binding(status=DeviceBindingStatus.ACTIVE.value)
        mock_device_service.get_device.return_value = mock_binding
        svc._device_service_provider = lambda: mock_device_service

        result = svc.stop_bot(bot_id="bot001", user_id="user001")

        mock_device_service.release_device.assert_called_once()
        svc._repository.update_by_owner.assert_called_once_with(
            "bot001", "user001",
            {"status": "PENDING", "binding_id": None, "device_id": None}
        )
        assert result is True

    def test_releases_stopped_device_and_resets_status(self):
        """STOPPED binding is finalized before restart allocates a replacement."""
        svc = _make_service()
        bot = _make_bot()
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_device_service = MagicMock()
        mock_device_service.get_device.return_value = _make_binding(
            status=DeviceBindingStatus.STOPPED.value,
        )
        svc._device_service_provider = lambda: mock_device_service

        result = svc.stop_bot(bot_id="bot001", user_id="user001")

        mock_device_service.release_device.assert_called_once()
        svc._repository.update_by_owner.assert_called_once_with(
            "bot001", "user001",
            {"status": "PENDING", "binding_id": None, "device_id": None},
        )
        assert result is True

    def test_returns_true_when_no_binding(self):
        """没有 binding_id 时跳过释放，直接重置状态，返回 True。"""
        svc = _make_service()
        bot = _make_bot(binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot

        result = svc.stop_bot(bot_id="bot001", user_id="user001")

        svc._repository.update_by_owner.assert_called_once_with(
            "bot001", "user001",
            {"status": "PENDING", "binding_id": None, "device_id": None}
        )
        assert result is True

    def test_bot_not_found_error_treated_as_already_released(self):
        """get_device 抛出 BotNotFoundError 时视为已释放，返回 True。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_device_service = MagicMock()
        mock_device_service.get_device.side_effect = BotNotFoundError("binding not found")
        svc._device_service_provider = lambda: mock_device_service

        result = svc.stop_bot(bot_id="bot001", user_id="user001")

        svc._repository.update_by_owner.assert_called_once()
        assert result is True

    def test_device_release_error_returns_false_but_still_resets_status(self):
        """设备释放失败时 status 仍重置，但返回 False。"""
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_device_service = MagicMock()
        mock_binding = _make_binding(status=DeviceBindingStatus.ACTIVE.value)
        mock_device_service.get_device.return_value = mock_binding
        mock_device_service.release_device.side_effect = RuntimeError("network error")
        svc._device_service_provider = lambda: mock_device_service

        result = svc.stop_bot(bot_id="bot001", user_id="user001")

        # status 仍然重置
        svc._repository.update_by_owner.assert_called_once_with(
            "bot001", "user001",
            {"status": "PENDING", "binding_id": None, "device_id": None}
        )
        assert result is False

    def test_invalid_device_status_skips_release(self):
        """设备状态为 RELEASED 时抛 InvalidDeviceStatusError，跳过释放，返回 True。"""
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_device_service = MagicMock()
        mock_binding = _make_binding(status=DeviceBindingStatus.ACTIVE.value)
        mock_device_service.get_device.return_value = mock_binding
        mock_device_service.release_device.side_effect = InvalidDeviceStatusError("already released")
        svc._device_service_provider = lambda: mock_device_service

        result = svc.stop_bot(bot_id="bot001", user_id="user001")

        svc._repository.update_by_owner.assert_called_once()
        assert result is True


# ===========================================================================
# start_bot 测试
# ===========================================================================


class TestStartBot:
    """BotService.start_bot()"""

    def test_raises_when_user_id_missing(self):
        """user_id 为空时抛出 BotServiceError。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

        svc = _make_service()
        with pytest.raises(BotServiceError, match="User ID is required"):
            svc.start_bot(bot_id="bot001", user_id="")

    def test_raises_when_bot_not_found(self):
        """bot 不存在时抛出 BotNotFoundError。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None

        with pytest.raises(BotNotFoundError):
            svc.start_bot(bot_id="bot001", user_id="user001")

    def test_triggers_async_allocation(self):
        """正常流程：触发异步设备分配并返回 bot 记录。"""
        svc = _make_service()
        bot = _make_bot()
        updated_bot = {**bot, "status": "PENDING"}
        svc._repository.get_by_id_and_owner.side_effect = [bot, updated_bot]

        svc._passport_plugin.query_token.return_value = None

        with patch.object(svc, "_allocate_device_async") as mock_alloc:
            result = svc.start_bot(bot_id="bot001", user_id="user001")

        mock_alloc.assert_called_once()
        assert result["bot_id"] == "bot001"

    def test_passport_token_failure_non_blocking(self):
        """queryToken 抛异常时不阻塞 start_bot，正常继续。"""
        svc = _make_service()
        bot = _make_bot()
        updated_bot = {**bot}
        svc._repository.get_by_id_and_owner.side_effect = [bot, updated_bot]

        svc._passport_plugin.query_token.side_effect = RuntimeError("token service down")

        with patch.object(svc, "_allocate_device_async") as mock_alloc:
            result = svc.start_bot(bot_id="bot001", user_id="user001")

        # 不抛异常，继续分配
        mock_alloc.assert_called_once()
        assert result is not None


# ===========================================================================
# start_bot + BCN 注册条件 (语雀 doc 548864073)
# ===========================================================================


class TestStartBotBcnRegister:
    """start_bot BCN Provider 注册条件测试。"""

    def test_claude_code_triggers_register_provider_bot(self):
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        bot["bot_desc"] = "summary"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
            name="TestBot",
            summary="summary",
        )
        # 注册成功 → ext 中只标记一个 bcn_registered=True 的布尔, 不落 token / uuid
        mock_ext.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            ext_update={"bcn_registered": True},
        )

    def test_claude_code_application_coding_does_not_trigger_bcn_register(self):
        svc = _make_service()
        svc._bcn_service = MagicMock()
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "applicationCoding"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_not_called()

    def test_claude_code_personal_coding_triggers_register_provider_bot(self):
        # active_engine=claude_code + template_type=personalCoding 时 start_bot 会调 BCN 注册接口。
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u-cc-personal-coding",
            "bot_runtime_token": "tok",
        }
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "personalCoding"
        bot["bot_desc"] = "personal summary"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
            name="TestBot",
            summary="personal summary",
        )
        mock_ext.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            ext_update={"bcn_registered": True},
        )

    def test_aicoding_personal_coding_triggers_register_provider_bot(self):
        # active_engine=aicoding + template_type=personalCoding 时 start_bot 会调 BCN 注册接口。
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u-aicoding-personal-coding",
            "bot_runtime_token": "tok",
        }
        bot = _make_bot()
        bot["active_engine"] = "aicoding"
        bot["template_type"] = "personalCoding"
        bot["bot_desc"] = "aicoding personal summary"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
            name="TestBot",
            summary="aicoding personal summary",
        )
        mock_ext.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            ext_update={"bcn_registered": True},
        )

    def test_claude_code_missing_template_type_does_not_trigger_bcn_register(self):
        svc = _make_service()
        svc._bcn_service = MagicMock()
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_not_called()

    def test_non_claude_code_does_not_trigger_bcn_register(self):
        svc = _make_service()
        svc._bcn_service = MagicMock()
        bot = _make_bot()  # active_engine="moltis"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_not_called()

    def test_teclaw_triggers_register_provider_bot(self):
        # active_engine=teclaw 时 start_bot 会调 BCN 注册接口 (与 claude_code 一致, 所有 bot_type).
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u-teclaw",
            "bot_runtime_token": "tok",
        }
        bot = _make_bot()
        bot["active_engine"] = "teclaw"
        bot["bot_desc"] = "summary"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
            name="TestBot",
            summary="summary",
        )
        mock_ext.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            ext_update={"bcn_registered": True},
        )

    def test_openclaw_service_bot_triggers_register_provider_bot(self):
        # 触发条件扩展: active_engine=openclaw + bot_type=service 也要走 BCN 注册.
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u-openclaw-svc",
            "bot_runtime_token": "tok",
        }
        bot = _make_bot()
        bot["active_engine"] = "openclaw"
        bot["bot_type"] = "service"
        bot["bot_desc"] = "svc-summary"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
            name="TestBot",
            summary="svc-summary",
        )
        mock_ext.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            ext_update={"bcn_registered": True},
        )

    def test_openclaw_personal_bot_does_not_trigger_bcn_register(self):
        # 反例: active_engine=openclaw + bot_type=personal 不应触发 BCN 注册.
        svc = _make_service()
        svc._bcn_service = MagicMock()
        bot = _make_bot()
        bot["active_engine"] = "openclaw"
        bot["bot_type"] = "personal"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_not_called()

    def test_bcn_register_failure_does_not_block_start(self):
        from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError

        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.side_effect = BcnServiceError("bcn down")
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async") as mock_alloc, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            result = svc.start_bot(bot_id="bot001", user_id="user001")

        # BCN 失败不影响主流程
        mock_alloc.assert_called_once()
        assert result is not None

    def test_skipped_register_does_not_call_update_ext(self):
        # dev 环境 register_provider_bot 返回 skipped=True; 也不应触发 update_bot_ext
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "skipped": True,
            "provider_bot_ref": "bot001:user001",
            "bot_runtime_token": "",
        }
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        mock_ext.assert_not_called()

    def test_register_only_writes_bcn_registered_does_not_overwrite_other_ext(self):
        # 回归: update_bot_ext 是局部合并 (dict.update), 不会清掉其他 ext 字段.
        # 这里通过断言 ext_update payload 只含 bcn_registered, 防止以后误改 BCN 注册
        # 路径时混入其它字段而无意覆盖.
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }
        bot = _make_bot(ext={"existing_key": "existing_value", "passport": "x"})
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.start_bot(bot_id="bot001", user_id="user001")

        mock_ext.assert_called_once()
        ext_update = mock_ext.call_args.kwargs["ext_update"]
        # 严格只含 bcn_registered 一个键, 没有 token / uuid / 其它键
        assert ext_update == {"bcn_registered": True}

    def test_drm_disabled_skips_register(self):
        # DRM 开关关闭 → 即便是 claude_code, 也不调 BCN, 也不写 ext
        svc = _make_service()
        svc._bcn_service = MagicMock()
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async"), \
             patch.object(svc, "update_bot_ext") as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_not_called()
        mock_ext.assert_not_called()

    def test_drm_default_off_skips_register_when_drm_unreachable(self):
        # DRM 读取异常 → 默认关闭, 不注册
        svc = _make_service()
        svc._bcn_service = MagicMock()
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        # 不打 patch _is_claude_code_bcn_register_enabled, 让真实方法跑.
        # 单测 DRM seam 默认 reader 返回 None → enabled False → 不注册.
        with patch.object(svc, "_allocate_device_async"):
            svc.start_bot(bot_id="bot001", user_id="user001")

        svc._bcn_service.register_provider_bot.assert_not_called()

    def test_drm_helper_unset_returns_false(self):
        # DRM 未配置 / 不可用 (reader 返回 None) → False.
        svc = _make_service()
        svc._drm_reader.read.return_value = None
        assert svc._is_claude_code_bcn_register_enabled() is False

    def test_drm_helper_returns_true_when_value_explicitly_on(self):
        # 覆盖正常 enabled 路径 (raw_value 非空且匹配 truthy 集合).
        svc = _make_service()
        svc._drm_reader.read.return_value = "true"
        assert svc._is_claude_code_bcn_register_enabled() is True

    def test_drm_helper_returns_false_for_unknown_value(self):
        # raw_value 既非空也非 truthy 集合 → False
        svc = _make_service()
        svc._drm_reader.read.return_value = "maybe"
        assert svc._is_claude_code_bcn_register_enabled() is False

    def test_register_update_ext_failure_does_not_block_start(self):
        # 注册成功但 update_bot_ext 抛错 → warning fallback, 主流程不挂
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async") as mock_alloc, \
             patch.object(svc, "update_bot_ext", side_effect=RuntimeError("db down")) as mock_ext, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            result = svc.start_bot(bot_id="bot001", user_id="user001")

        # update_bot_ext 被尝试过, 异常被吞, _allocate_device_async 仍然被调
        mock_ext.assert_called_once()
        mock_alloc.assert_called_once()
        assert result is not None

    def test_register_unexpected_exception_does_not_block_start(self):
        # bcn_service.register_provider_bot 抛非 BcnServiceError 的异常 → 通用 Exception 兜底
        svc = _make_service()
        svc._bcn_service = MagicMock()
        svc._bcn_service.register_provider_bot.side_effect = RuntimeError("kaboom")
        bot = _make_bot()
        bot["active_engine"] = "claude_code"
        bot["template_type"] = "normalCC"
        svc._repository.get_by_id_and_owner.side_effect = [bot, bot]

        with patch.object(svc, "_allocate_device_async") as mock_alloc, \
             patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            result = svc.start_bot(bot_id="bot001", user_id="user001")

        mock_alloc.assert_called_once()
        assert result is not None


# ===========================================================================
# restart_bot 测试
# ===========================================================================


class TestRestartBot:
    """BotService.restart_bot() — 验证 stop_bot + start_bot 组合行为。"""

    def test_raises_when_user_id_missing(self):
        """user_id 为空时抛出 BotServiceError。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

        svc = _make_service()
        with pytest.raises(BotServiceError, match="User ID is required"):
            svc.restart_bot(bot_id="bot001", user_id="")

    def test_calls_stop_then_start(self):
        """restart_bot 先调 stop_bot 再调 start_bot。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="ACTIVE",
            binding_id=None,
        )
        call_order = []

        updated_bot = _make_bot(status="PENDING")

        with patch.object(svc, "stop_bot", side_effect=lambda **kw: call_order.append("stop") or True), \
             patch.object(svc, "start_bot", side_effect=lambda **kw: call_order.append("start") or updated_bot):
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert call_order == ["stop", "start"]
        assert result == updated_bot

    def test_stop_failure_adds_restart_warning(self):
        """stop_bot 返回 False（设备释放失败）时，返回值含 restart_warning。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="ACTIVE",
            binding_id=None,
        )
        updated_bot = _make_bot(status="PENDING")

        with patch.object(svc, "stop_bot", return_value=False), \
             patch.object(svc, "start_bot", return_value=updated_bot):
            result = svc.restart_bot(bot_id="bot001", user_id="user001")

        assert "restart_warning" in result

    def test_stop_bot_exception_propagates(self):
        """stop_bot 抛异常时 restart_bot 也应抛出。"""
        from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot(
            status="ACTIVE",
            binding_id=None,
        )

        with patch.object(svc, "stop_bot", side_effect=BotNotFoundError("not found")):
            with pytest.raises(BotNotFoundError):
                svc.restart_bot(bot_id="bot001", user_id="user001")
