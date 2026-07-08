"""Unit tests for BotService misc methods.

Covers: get_bot, switch_engine, delete_bot, update_bot_ext,
check_bot_name_exists, generate_bot_id.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
    BotServiceError,
    generate_bot_id,
)


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
    engine_types: list[str] | None = None,
    active_engine: str = "moltis",
    ext: dict | None = None,
    bot_type: str = "personal",
    template_type: str | None = None,
) -> dict:
    result = {
        "bot_id": bot_id,
        "owner_id": owner_id,
        "status": status,
        "binding_id": binding_id,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "engine_types": engine_types or ["moltis", "openclaw"],
        "active_engine": active_engine,
        "bot_name": "TestBot",
        "ext": ext,
        "bot_type": bot_type,
    }
    if template_type is not None:
        result["template_type"] = template_type
    return result


def _make_service() -> BotService:
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._passport_plugin = MagicMock()
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = lambda: MagicMock()
    svc._restart_lock_repo = MagicMock()
    svc._template_service = MagicMock()
    svc._cleanup_service = MagicMock()
    svc._path_factory = MagicMock()
    svc._bcn_service = MagicMock()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="", aixcore_base_url_pre="")
    svc._collaborator_repo = MagicMock()
    svc._allocation_config = MagicMock()
    svc._device_binding_repo = MagicMock()
    svc._skill_set_factory = MagicMock()
    svc._bot_publish_repo = MagicMock()
    svc._oss_record_repo = MagicMock()
    return svc


# ===========================================================================
# generate_bot_id
# ===========================================================================


class TestGenerateBotId:

    def test_first_bot_returns_default(self):
        repo = MagicMock()
        repo.exists_by_owner_and_bot_id.return_value = False
        assert generate_bot_id("user001", repo) == "default"

    def test_second_bot_returns_date_based_id(self):
        repo = MagicMock()
        repo.exists_by_owner_and_bot_id.return_value = True
        bot_id = generate_bot_id("user001", repo)
        assert bot_id != "default"
        assert len(bot_id) == 17  # yyyymmdd + _ + 8 chars

    def test_generated_id_format(self):
        repo = MagicMock()
        repo.exists_by_owner_and_bot_id.return_value = True
        bot_id = generate_bot_id("user001", repo)
        parts = bot_id.split("_")
        assert len(parts) == 2
        assert len(parts[0]) == 8  # date part
        assert len(parts[1]) == 8  # random part
        assert parts[0].isdigit()


# ===========================================================================
# get_bot
# ===========================================================================


class TestGetBot:

    def test_raises_when_bot_not_found(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None
        with pytest.raises(BotNotFoundError):
            svc.get_bot("bot001", "user001")

    def test_returns_bot_without_binding(self):
        svc = _make_service()
        bot = _make_bot(binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        assert result["bot_id"] == "bot001"
        assert "device_binding" not in result

    def test_attaches_device_binding_when_present(self):
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_binding = MagicMock()
        mock_binding.to_dict.return_value = {"id": 42, "status": "ACTIVE"}
        mock_device_service = MagicMock()
        mock_device_service.get_device.return_value = mock_binding
        svc._device_service_provider = lambda: mock_device_service
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        assert result["device_binding"] == {"id": 42, "status": "ACTIVE"}

    def test_arca_device_props_not_enriched(self):
        """社区零 arca: get_bot must NOT inject tenant_idx/tenant into arca
        device_props — the raw sandbox_id is returned untouched (enrichment
        moved to corp)."""
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_binding = MagicMock()
        mock_binding.to_dict.return_value = {
            "id": 42,
            "device_id": "arca-raw-id",
            "device_provider": "arca",
            "device_props": {"sandbox_id": "arca-raw-id@2"},
        }
        mock_device_service = MagicMock()
        mock_device_service.get_device.return_value = mock_binding
        svc._device_service_provider = lambda: mock_device_service
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        props = result["device_binding"]["device_props"]
        assert "tenant" not in props
        assert "tenant_idx" not in props
        assert props["sandbox_id"] == "arca-raw-id@2"

    def test_device_summary_for_arca_bot_no_sandbox_id(self):
        """device_props without sandbox_id → no enrichment."""
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_binding = MagicMock()
        mock_binding.to_dict.return_value = {
            "id": 42,
            "device_id": "arca-raw-id",
            "device_provider": "arca",
            "device_props": {},
        }
        mock_device_service = MagicMock()
        mock_device_service.get_device.return_value = mock_binding
        svc._device_service_provider = lambda: mock_device_service
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        props = result["device_binding"]["device_props"]
        assert "tenant_idx" not in props
        assert "tenant" not in props

    def test_device_summary_for_baas_bot(self):
        """baas provider: device_props unchanged (no arca enrichment)."""
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_binding = MagicMock()
        mock_binding.to_dict.return_value = {
            "id": 42,
            "device_id": "baas-bot-uuid-xyz",
            "device_provider": "baas",
            "device_props": {
                "bot_uuid": "baas-bot-uuid-xyz",
                "device_from": "baas",
            },
        }
        mock_device_service = MagicMock()
        mock_device_service.get_device.return_value = mock_binding
        svc._device_service_provider = lambda: mock_device_service
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        props = result["device_binding"]["device_props"]
        assert "tenant_idx" not in props
        assert "tenant" not in props

    def test_device_summary_absent_when_no_binding(self):
        svc = _make_service()
        bot = _make_bot(binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        assert "_device_summary" not in result

    def test_device_summary_absent_on_binding_exception(self):
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_device_service = MagicMock()
        mock_device_service.get_device.side_effect = RuntimeError("device db down")
        svc._device_service_provider = lambda: mock_device_service
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        assert "_device_summary" not in result

    def test_device_binding_exception_does_not_propagate(self):
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_device_service = MagicMock()
        mock_device_service.get_device.side_effect = RuntimeError("device db down")
        svc._device_service_provider = lambda: mock_device_service
        svc._template_service.get_template.return_value = None

        result = svc.get_bot("bot001", "user001")
        assert result["bot_id"] == "bot001"
        assert "device_binding" not in result

    def test_attaches_template_config(self):
        svc = _make_service()
        bot = _make_bot(binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._template_service.get_template.return_value = {"ext": {"key": "val"}}

        result = svc.get_bot("bot001", "user001")
        assert result["template_config"] == {"key": "val"}

    def test_template_exception_does_not_propagate(self):
        svc = _make_service()
        bot = _make_bot(binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._template_service.get_template.side_effect = RuntimeError("template db down")

        result = svc.get_bot("bot001", "user001")
        assert result["bot_id"] == "bot001"
        assert "template_config" not in result


# ===========================================================================
# switch_engine
# ===========================================================================


class TestSwitchEngine:

    def test_raises_when_user_id_empty(self):
        svc = _make_service()
        with pytest.raises(BotServiceError, match="User ID is required"):
            svc.switch_engine("bot001", "", "openclaw")

    def test_raises_for_invalid_engine_type(self):
        svc = _make_service()
        with patch(
            "agentclaw.community.core.bot_management.services.bot_service._get_engine_types",
            return_value=["moltis", "openclaw"],
        ):
            with pytest.raises(BotServiceError, match="Invalid engine type"):
                svc.switch_engine("bot001", "user001", "nonexistent")

    def test_raises_when_bot_not_found(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None
        with patch(
            "agentclaw.community.core.bot_management.services.bot_service._get_engine_types",
            return_value=["moltis", "openclaw"],
        ):
            with pytest.raises(BotNotFoundError):
                svc.switch_engine("bot001", "user001", "openclaw")

    def test_raises_when_engine_not_enabled_for_bot(self):
        svc = _make_service()
        bot = _make_bot(engine_types=["moltis"])
        svc._repository.get_by_id_and_owner.return_value = bot
        with patch(
            "agentclaw.community.core.bot_management.services.bot_service._get_engine_types",
            return_value=["moltis", "openclaw"],
        ):
            with pytest.raises(BotServiceError, match="not enabled"):
                svc.switch_engine("bot001", "user001", "openclaw")

    def test_switches_engine_successfully(self):
        svc = _make_service()
        bot = _make_bot(engine_types=["moltis", "openclaw"])
        updated_bot = {**bot, "active_engine": "openclaw", "binding_id": None}
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._repository.update_by_owner.return_value = updated_bot

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service._get_engine_types",
            return_value=["moltis", "openclaw"],
        ):
            result = svc.switch_engine("bot001", "user001", "openclaw")

        svc._repository.update_by_owner.assert_called_once()
        call_args = svc._repository.update_by_owner.call_args
        assert call_args[0][2]["active_engine"] == "openclaw"
        assert result["active_engine"] == "openclaw"

    def test_update_failure_wraps_exception(self):
        svc = _make_service()
        bot = _make_bot(engine_types=["moltis", "openclaw"])
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._repository.update_by_owner.side_effect = RuntimeError("db error")

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service._get_engine_types",
            return_value=["moltis", "openclaw"],
        ):
            with pytest.raises(BotServiceError, match="Failed to switch engine"):
                svc.switch_engine("bot001", "user001", "openclaw")


# ===========================================================================
# delete_bot
# ===========================================================================


class TestDeleteBot:

    def test_raises_when_user_id_empty(self):
        svc = _make_service()
        with pytest.raises(BotServiceError, match="User ID is required"):
            svc.delete_bot("bot001", "")

    def test_raises_when_bot_not_found(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None
        with pytest.raises(BotNotFoundError):
            svc.delete_bot("bot001", "user001")

    def test_deletes_bot_without_binding(self):
        svc = _make_service()
        bot = _make_bot(binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._passport_plugin.destroy_passport.return_value = None
        svc._repository.soft_delete_by_owner.return_value = True

        result = svc.delete_bot("bot001", "user001")
        assert result is True
        svc._repository.soft_delete_by_owner.assert_called_once_with("bot001", "user001")

    def test_syncs_bcn_provider_delete_after_soft_delete(self):
        svc = _make_service()
        bot = _make_bot(bot_id="bot001", binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._passport_plugin.destroy_passport.return_value = None
        svc._repository.soft_delete_by_owner.return_value = True

        result = svc.delete_bot("bot001", "user001")

        assert result is True
        svc._bcn_service.delete_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
        )

    def test_bcn_provider_delete_failure_does_not_block_delete(self):
        svc = _make_service()
        bot = _make_bot(bot_id="bot001", binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._passport_plugin.destroy_passport.return_value = None
        svc._repository.soft_delete_by_owner.return_value = True
        svc._bcn_service.delete_provider_bot.side_effect = RuntimeError("bcn down")

        with patch("agentclaw.community.core.bot_management.services.bot_service.logger") as mock_logger:
            result = svc.delete_bot("bot001", "user001")

        assert result is True
        svc._bcn_service.delete_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
        )
        mock_logger.error.assert_called()

    def test_skips_bcn_provider_delete_when_soft_delete_fails(self):
        svc = _make_service()
        bot = _make_bot(bot_id="bot001", binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._passport_plugin.destroy_passport.return_value = None
        svc._repository.soft_delete_by_owner.return_value = False

        with pytest.raises(BotNotFoundError):
            svc.delete_bot("bot001", "user001")

        svc._bcn_service.delete_provider_bot.assert_not_called()

    def test_releases_device_before_deleting(self):
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_binding = MagicMock()
        mock_binding.status = "ACTIVE"
        mock_device_service = MagicMock()
        mock_device_service.get_device.return_value = mock_binding
        svc._device_service_provider = lambda: mock_device_service
        svc._passport_plugin.destroy_passport.return_value = None
        svc._repository.soft_delete_by_owner.return_value = True

        result = svc.delete_bot("bot001", "user001")
        assert result is True
        mock_device_service.release_device.assert_called_once()

    def test_device_release_failure_blocks_deletion(self):
        svc = _make_service()
        bot = _make_bot(binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        mock_binding = MagicMock()
        mock_binding.status = "ACTIVE"
        mock_device_service = MagicMock()
        mock_device_service.get_device.return_value = mock_binding
        mock_device_service.release_device.side_effect = RuntimeError("release failed")
        svc._device_service_provider = lambda: mock_device_service

        with pytest.raises(BotServiceError, match="设备释放失败"):
            svc.delete_bot("bot001", "user001")
        svc._repository.soft_delete_by_owner.assert_not_called()

    def test_passport_destroy_failure_blocks_deletion(self):
        from agentclaw.community.plugin_api.passport import PassportError

        svc = _make_service()
        bot = _make_bot(binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._passport_plugin.destroy_passport.side_effect = PassportError("auth fail")

        with pytest.raises(BotServiceError, match="销毁 Passport 失败"):
            svc.delete_bot("bot001", "user001")
        svc._repository.soft_delete_by_owner.assert_not_called()

    def test_default_bot_delete_is_forbidden(self):
        """default bot 是用户的常驻默认 Bot,不允许删除(重启请走 restart_bot)。

        拦截必须发生在 release_device / destroy_passport / soft_delete 之前,
        否则会误销毁 agent 许可证 (Passport) 并重置引擎配置 (openclaw.json)。
        """
        svc = _make_service()
        bot = _make_bot(bot_id="default", binding_id=42)
        svc._repository.get_by_id_and_owner.return_value = bot

        with pytest.raises(BotServiceError, match="default bot 不允许删除"):
            svc.delete_bot("default", "user001")

        # 许可证未销毁、设备未释放、bot 记录未删、脏数据未清理
        svc._passport_plugin.destroy_passport.assert_not_called()
        svc._repository.soft_delete_by_owner.assert_not_called()
        svc._cleanup_service.cleanup_single_bot_data.assert_not_called()

    def test_non_default_bot_triggers_cleanup(self):
        svc = _make_service()
        bot = _make_bot(bot_id="mybot", binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._passport_plugin.destroy_passport.return_value = None
        svc._repository.soft_delete_by_owner.return_value = True
        svc._cleanup_service.cleanup_single_bot_data.return_value = {
            "skills_deleted": 1, "skill_sets_deleted": 0, "resources_deleted": 0,
        }

        result = svc.delete_bot("mybot", "user001")
        assert result is True
        svc._cleanup_service.cleanup_single_bot_data.assert_called_once_with("mybot", "user001")

    def test_cleanup_failure_does_not_block_delete(self):
        svc = _make_service()
        bot = _make_bot(bot_id="mybot", binding_id=None)
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._passport_plugin.destroy_passport.return_value = None
        svc._repository.soft_delete_by_owner.return_value = True
        svc._cleanup_service.cleanup_single_bot_data.side_effect = RuntimeError("cleanup crash")

        result = svc.delete_bot("mybot", "user001")
        assert result is True


# ===========================================================================
# update_bot_ext
# ===========================================================================


class TestUpdateBotExt:

    def test_merges_ext_update_into_existing(self):
        svc = _make_service()
        bot = _make_bot(ext={"key1": "val1"})
        with patch.object(svc, "get_bot", return_value=bot):
            svc.update_bot_ext("bot001", "user001", {"key2": "val2"})

        svc._repository.update_by_owner.assert_called_once()
        updated_ext = svc._repository.update_by_owner.call_args[0][2]["ext"]
        assert updated_ext == {"key1": "val1", "key2": "val2"}

    def test_handles_none_ext(self):
        svc = _make_service()
        bot = _make_bot(ext=None)
        with patch.object(svc, "get_bot", return_value=bot):
            svc.update_bot_ext("bot001", "user001", {"new_key": True})

        updated_ext = svc._repository.update_by_owner.call_args[0][2]["ext"]
        assert updated_ext == {"new_key": True}

    def test_handles_ext_as_json_string(self):
        svc = _make_service()
        bot = _make_bot()
        bot["ext"] = json.dumps({"existing": "data"})
        with patch.object(svc, "get_bot", return_value=bot):
            svc.update_bot_ext("bot001", "user001", {"added": 1})

        updated_ext = svc._repository.update_by_owner.call_args[0][2]["ext"]
        assert updated_ext == {"existing": "data", "added": 1}

    def test_handles_invalid_json_string_ext(self):
        svc = _make_service()
        bot = _make_bot()
        bot["ext"] = "not-valid-json"
        with patch.object(svc, "get_bot", return_value=bot):
            svc.update_bot_ext("bot001", "user001", {"key": "val"})

        updated_ext = svc._repository.update_by_owner.call_args[0][2]["ext"]
        assert updated_ext == {"key": "val"}

    def test_overwrites_existing_key(self):
        svc = _make_service()
        bot = _make_bot(ext={"key": "old"})
        with patch.object(svc, "get_bot", return_value=bot):
            svc.update_bot_ext("bot001", "user001", {"key": "new"})

        updated_ext = svc._repository.update_by_owner.call_args[0][2]["ext"]
        assert updated_ext["key"] == "new"


# ===========================================================================
# check_bot_name_exists
# ===========================================================================


class TestCheckBotNameExists:

    def test_returns_false_for_none(self):
        svc = _make_service()
        assert svc.check_bot_name_exists(None) is False

    def test_returns_false_for_empty_string(self):
        svc = _make_service()
        assert svc.check_bot_name_exists("") is False

    def test_returns_false_for_whitespace_only(self):
        svc = _make_service()
        assert svc.check_bot_name_exists("   ") is False

    def test_delegates_to_repository(self):
        svc = _make_service()
        svc._repository.exists_by_bot_name.return_value = True
        assert svc.check_bot_name_exists("MyBot") is True
        svc._repository.exists_by_bot_name.assert_called_once_with("MyBot")

    def test_strips_name_before_check(self):
        svc = _make_service()
        svc._repository.exists_by_bot_name.return_value = False
        svc.check_bot_name_exists("  MyBot  ")
        svc._repository.exists_by_bot_name.assert_called_once_with("MyBot")

    def test_returns_false_when_not_exists(self):
        svc = _make_service()
        svc._repository.exists_by_bot_name.return_value = False
        assert svc.check_bot_name_exists("NewBot") is False


# ===========================================================================
# list_bots
# ===========================================================================


class TestListBots:

    def test_returns_total_and_items(self):
        svc = _make_service()
        svc._repository.list_by_entity.return_value = (2, [{"bot_id": "a"}, {"bot_id": "b"}])

        result = svc.list_bots(entity_id="ent1", entity_type="staff", page=1, page_size=10)
        assert result["total"] == 2
        assert len(result["items"]) == 2

    def test_passes_parameters_to_repository(self):
        svc = _make_service()
        svc._repository.list_by_entity.return_value = (0, [])

        svc.list_bots(entity_id="ent1", entity_type="dept", page=3, page_size=5)
        svc._repository.list_by_entity.assert_called_once_with(
            entity_id="ent1", entity_type="dept", page=3, page_size=5,
        )

    def test_default_pagination(self):
        svc = _make_service()
        svc._repository.list_by_entity.return_value = (0, [])

        svc.list_bots()
        svc._repository.list_by_entity.assert_called_once_with(
            entity_id=None, entity_type=None, page=1, page_size=20,
        )
