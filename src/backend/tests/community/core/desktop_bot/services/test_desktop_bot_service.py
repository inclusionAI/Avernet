"""Unit tests for DesktopBotService."""
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.desktop_bot.services.desktop_bot_service import (
    DesktopBotService,
    DesktopBotServiceError,
    _format_datetime,
    _generate_request_id,
    _to_device_display_status,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
from agentclaw.community.plugin_api.passport import PassportError


class TestHelpers:
    def test_generate_request_id_consistent(self):
        a = _generate_request_id("bot1", "entity1", "staff", "dev", "create")
        b = _generate_request_id("bot1", "entity1", "staff", "dev", "create")
        assert a == b
        assert len(a) == 32

    def test_generate_request_id_different_per_action(self):
        create = _generate_request_id("bot1", "e1", "staff", "dev", "create")
        restart = _generate_request_id("bot1", "e1", "staff", "dev", "restart")
        assert create != restart

    def test_to_device_display_status_maps_correctly(self):
        assert _to_device_display_status("ONLINE") == "ACTIVE"
        assert _to_device_display_status("OFFLINE") == "OFFLINE"
        assert _to_device_display_status("DISABLED") == "RELEASED"

    def test_to_device_display_status_case_insensitive(self):
        assert _to_device_display_status("online") == "ACTIVE"
        assert _to_device_display_status("Online") == "ACTIVE"

    def test_to_device_display_status_unknown_passthrough(self):
        assert _to_device_display_status("UNKNOWN") == "UNKNOWN"

    def test_format_datetime_none_returns_empty(self):
        assert _format_datetime(None) == ""

    def test_format_datetime_string_returns_same(self):
        assert _format_datetime("2026-05-13T10:00:00") == "2026-05-13T10:00:00"

    def test_format_datetime_datetime_object(self):
        from datetime import datetime
        dt = datetime(2026, 5, 13, 10, 0, 0)
        assert _format_datetime(dt) == "2026-05-13T10:00:00"

    def test_format_datetime_fallback_str(self):
        assert _format_datetime(123) == "123"


class TestListDirectory:
    def test_returns_tree(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "code": 0,
            "data": {
                "name": "Desktop",
                "children": [
                    {"name": "file1.txt"},
                    {"name": "folder1", "children": [{"name": "nested.txt"}]},
                ],
            },
        }

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            result = service.list_directory(machine_id="m-001", dir="~/Desktop")

        assert result["name"] == "Desktop"
        assert len(result["children"]) == 2

    def test_passes_dir_param(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"code": 0, "data": {}}

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            service.list_directory(machine_id="m-001", dir="~/Documents")

        call_args = mock_client.return_value.__enter__.return_value.get.call_args
        assert call_args.kwargs["params"]["dir"] == "~/Documents"
        assert "res-dirs" in call_args[0][0]

    def test_api_error_raises(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"code": 1, "message": "Not found"}

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            with pytest.raises(DesktopBotServiceError, match="BaaS API error: Not found"):
                service.list_directory(machine_id="m-001")

    def test_empty_data_returns_empty_dict(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"code": 0, "data": None}

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            result = service.list_directory(machine_id="m-001")

        assert result == {}


class TestListDevices:
    def test_returns_devices(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "code": 0,
            "data": [
                {
                    "machine_id": "m-001",
                    "status": "ONLINE",
                    "last_heartbeat": "2026-05-13T10:00:00",
                    "machine_info": {
                        "machine_name": "mbp-001",
                        "ip_address": "10.0.0.1",
                        "os_version": "macOS 15",
                        "created_at": "2026-05-01",
                    },
                },
            ],
        }

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            total, items = service.list_devices(user_id="u001")

        assert total == 1
        assert items[0]["machine_id"] == "m-001"
        assert items[0]["machine_name"] == "mbp-001"
        assert items[0]["status"] == "ACTIVE"
        assert items[0]["last_online_at"] == "2026-05-13T10:00:00"

    def test_filters_by_status(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "code": 0,
            "data": [
                {
                    "machine_id": "m-001", "status": "ONLINE",
                    "last_heartbeat": None, "machine_info": {},
                },
                {
                    "machine_id": "m-002", "status": "OFFLINE",
                    "last_heartbeat": None, "machine_info": {},
                },
            ],
        }

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            total, items = service.list_devices(user_id="u001", status="ACTIVE")

        assert total == 1
        assert items[0]["machine_id"] == "m-001"

    def test_pagination(self):
        service = _make_service()
        machines = [
            {"machine_id": f"m-{i:03d}", "status": "ONLINE",
             "last_heartbeat": None, "machine_info": {}}
            for i in range(25)
        ]
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"code": 0, "data": machines}

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            total, items = service.list_devices(user_id="u001", page=2, page_size=10)

        assert total == 25
        assert len(items) == 10

    def test_api_error_raises(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"code": 1, "message": "Boom"}

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            with pytest.raises(DesktopBotServiceError, match="BaaS API error: Boom"):
                service.list_devices(user_id="u001")


class TestCreate:
    def test_create_returns_authorization_when_no_agent_code(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].apply_agent_passport.return_value = {
            "token": "tk-123",
            # 无 agent_code，走两段式返回 authorization
        }

        result = service.apply_passport_before_create(
            bot={"bot_name": "My Bot", "bot_desc": "desc"},
            user_id="u001",
            machine_id="m-001",
        )

        assert result["need_authorization"] is True
        assert result["bot_id"].startswith("desktop_bot_")
        assert result["iframe_url"] is None
        assert result["redirect_url"] is None
        mocks["baas"].post_bots_api.assert_not_called()
        mocks["baas"].approve_publish.assert_not_called()
        mocks["binding_repo"].insert_binding.assert_not_called()
        mocks["bot_repo"].insert.assert_not_called()
        # verify device_token passed to passport
        passport_call = mocks["passport"].apply_agent_passport.call_args.kwargs
        assert passport_call["device_token"] == "dev-tok-001"

    def test_create_filters_local_mcp_codes_before_passport(self):
        service, mocks = _make_service_with_mocks()
        mocks["skill_set_factory"].create.return_value.get_bot_mcp_codes.return_value = [
            "mcp.remote.1",
            "hitl",
        ]
        mocks["passport"].apply_agent_passport.return_value = {
            "token": "tk-123",
        }

        service.apply_passport_before_create(
            bot={"bot_name": "My Bot", "bot_desc": "desc"},
            user_id="u001",
            machine_id="m-001",
        )

        passport_call = mocks["passport"].apply_agent_passport.call_args.kwargs
        assert passport_call["mcp_codes"] == ["mcp.remote.1"]

    def test_create_passport_empty_result_raises(self):
        """apply_agent_passport 返回空结果时应抛异常，不再 fallback 到首次申请。"""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].apply_agent_passport.return_value = None

        with pytest.raises(DesktopBotServiceError, match="Passport apply returned empty result"):
            service.apply_passport_before_create(
                bot={"bot_name": "B"}, user_id="u001", machine_id="m-001",
            )

    def test_create_passport_returns_authorization_links(self):
        """apply_agent_passport 返回 iframe_url/redirect_url 时，apply_passport_before_create
        应将其透传给前端。"""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].apply_agent_passport.return_value = {
            "agent_code": "",
            "iframe_url": "https://auth.example.com",
            "redirect_url": "https://redirect.example.com",
        }

        result = service.apply_passport_before_create(
            bot={"bot_name": "B"}, user_id="u001", machine_id="m-001",
        )

        assert result["need_authorization"] is True
        assert result["iframe_url"] == "https://auth.example.com"
        assert result["redirect_url"] == "https://redirect.example.com"

    def test_create_continue_success(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-continue-001",
        }
        mocks["baas"].post_bots_api.return_value = {
            "bot_uuid": "bu-continue", "publish_id": 99,
        }
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 20

        result = service.create_after_authorization(
            bot={"bot_id": "desktop_bot_002", "bot_name": "Continued Bot"},
            user_id="u001",
            machine_id="m-002",
        )

        assert result["bot_uuid"] == "bu-continue"
        assert result["binding_id"] == 20
        assert result["bot_id"] == "desktop_bot_002"
        mocks["passport"].query_agent_passport.assert_called_once_with(
            bot_id="desktop_bot_002", owner_workno="u001",
        )
        mocks["baas"].post_bots_api.assert_called_once()
        bot_insert_call = mocks["bot_repo"].insert.call_args[0][0]
        assert bot_insert_call["ext"]["passport"]["agent_code"] == "ac-continue-001"
        # ext 中应包含创建时写入的不可变部署信息
        assert bot_insert_call["ext"]["machine_id"] == "m-002"
        assert bot_insert_call["ext"]["mount_path"] == ""
        assert bot_insert_call["ext"]["migration_path"] == (
            "/home/admin/nfs/bot-data/desktop/desktop_bot_002"
        )
        assert bot_insert_call["ext"]["workspace_path"] == (
            "~/.teamclaw/boxes/desktop_bot_002"
        )
        assert bot_insert_call["ext"]["desktop_template_uuid"] == "desk-tpl-001"

    def test_create_continue_with_custom_mount_path(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_003", "bot_name": "B"},
            user_id="u001",
            machine_id="m-001",
            mount_path="/custom/mount",
        )

        call_kwargs = mocks["baas"].post_bots_api.call_args.kwargs
        payload = call_kwargs["payload"]
        assert payload["config"]["deploy_config"]["mount_path"] == "/custom/mount"
        # ext 中的 mount_path 应同步记录
        bot_insert_call = mocks["bot_repo"].insert.call_args[0][0]
        assert bot_insert_call["ext"]["mount_path"] == "/custom/mount"
        assert bot_insert_call["ext"]["machine_id"] == "m-001"

    def test_create_continue_with_custom_migration_path(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_003", "bot_name": "B"},
            user_id="u001",
            machine_id="m-001",
            migration_path="/custom/migration",
        )

        start_cmd_call = mocks["baas"]._get_start_cmd.call_args
        assert start_cmd_call.kwargs["migration_pat"] == "/custom/migration"

    def test_create_continue_empty_mount_path_treated_as_none(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_003", "bot_name": "B"},
            user_id="u001",
            machine_id="m-001",
            mount_path="   ",
        )

        call_kwargs = mocks["baas"].post_bots_api.call_args.kwargs
        payload = call_kwargs["payload"]
        assert "mount_path" not in payload["config"]["deploy_config"]
        # 空白 mount_path 应存为空字符串
        bot_insert_call = mocks["bot_repo"].insert.call_args[0][0]
        assert bot_insert_call["ext"]["mount_path"] == ""

    def test_create_continue_ext_stores_avatar_url(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-avatar",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-av", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={
                "bot_id": "desktop_bot_avatar",
                "bot_name": "Avatar Bot",
                "avatar_url": "https://img.example.com/bot.png",
            },
            user_id="u001",
            machine_id="m-avatar",
            mount_path="/data/workspace",
        )

        bot_insert_call = mocks["bot_repo"].insert.call_args[0][0]
        ext = bot_insert_call["ext"]
        assert ext["avatar_url"] == "https://img.example.com/bot.png"
        assert ext["machine_id"] == "m-avatar"
        assert ext["mount_path"] == "/data/workspace"
        assert ext["migration_path"] == (
            "/home/admin/nfs/bot-data/desktop/desktop_bot_avatar"
        )
        assert "publish_id" not in ext

    def test_create_continue_db_write_failure_raises(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.side_effect = Exception("DB down")

        with pytest.raises(DesktopBotServiceError, match="local write failed"):
            service.create_after_authorization(
                bot={"bot_id": "desktop_bot_003", "bot_name": "B"},
                user_id="u001",
                machine_id="m-001",
            )

    def test_create_continue_missing_bot_id_raises(self):
        service, mocks = _make_service_with_mocks()

        with pytest.raises(DesktopBotServiceError, match="bot_id is required"):
            service.create_after_authorization(
                bot={"bot_name": "No ID"}, user_id="u001", machine_id="m-001",
            )

    def test_create_continue_passport_not_found_raises(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = None

        with pytest.raises(DesktopBotServiceError, match="Passport not found"):
            service.create_after_authorization(
                bot={"bot_id": "desktop_bot_003"}, user_id="u001", machine_id="m-001",
            )

    def test_create_continue_approve_failure_raises(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {
            "bot_uuid": "bu-001", "publish_id": 5,
        }
        mocks["baas"].approve_publish.side_effect = Exception("approve boom")

        with pytest.raises(DesktopBotServiceError, match="approve publish failed"):
            service.create_after_authorization(
                bot={"bot_id": "desktop_bot_003"}, user_id="u001", machine_id="m-001",
            )

    def test_create_continue_no_publish_id_skips_approve(self):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001"}
        mocks["binding_repo"].insert_binding.return_value = 1

        result = service.create_after_authorization(
            bot={"bot_id": "desktop_bot_003"}, user_id="u001", machine_id="m-001",
        )

        assert result["bot_uuid"] == "bu-001"
        mocks["baas"].approve_publish.assert_not_called()

    def test_create_continue_without_desktop_template_uuid_raises(self):
        from agentclaw.community.di.config import BaasConfig
        from agentclaw.community.core.devices.services.device_service import DeviceService
        skill_set_service = MagicMock()
        skill_set_service.get_bot_mcp_codes.return_value = []
        skill_set_factory = MagicMock()
        skill_set_factory.create.return_value = skill_set_service
        mocks = {
            "baas": MagicMock(),
            "passport": MagicMock(),
            "binding_repo": MagicMock(),
            "bot_repo": MagicMock(),
            "device_service": MagicMock(spec=DeviceService),
        }
        service = DesktopBotService(
            baas_service=mocks["baas"],
            passport_plugin=mocks["passport"],
            device_binding_repo=mocks["binding_repo"],
            bot_repository=mocks["bot_repo"],
            baas_config=BaasConfig(desktop_template_uuid=""),
            device_service=mocks["device_service"],
            skill_set_factory=skill_set_factory,
        )
        service._fetch_machine_info = MagicMock(
            return_value={"device_token": "dev-tok-001"}
        )
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }

        with pytest.raises(DesktopBotServiceError, match="desktop_template_uuid is not configured"):
            service.create_after_authorization(
                bot={"bot_id": "desktop_bot_003"}, user_id="u001", machine_id="m-001",
            )


class TestCredentialsInDeployConfig:
    """Tests for .credentials fields passed via deploy_config."""

    def test_credentials_dict_in_deploy_config(self):
        """deploy_config should contain a 'credentials' dict with all required fields."""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-cred-001",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_cred", "bot_name": "Cred Bot"},
            user_id="u001",
            machine_id="m-001",
        )

        call_kwargs = mocks["baas"].post_bots_api.call_args.kwargs
        payload = call_kwargs["payload"]
        creds = payload["config"]["deploy_config"]["credentials"]

        assert creds["bot_id"] == "desktop_bot_cred"
        assert creds["owner_id"] == "u001"
        assert creds["entity_id"] == "staff_u001"
        assert creds["entity_type"] == "staff"
        assert creds["bot_type"] == "desktop"
        assert creds["agent_code"] == "ac-cred-001"
        assert creds["stage"] == "online"
        assert "version" not in creds

    def test_credentials_token_is_nonempty(self):
        """token field should be a non-empty string (secrets.token_urlsafe)."""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {"agent_code": "ac-001"}
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_tok", "bot_name": "T"},
            user_id="u001",
            machine_id="m-001",
        )

        payload = mocks["baas"].post_bots_api.call_args.kwargs["payload"]
        creds = payload["config"]["deploy_config"]["credentials"]
        assert isinstance(creds["token"], str)
        assert len(creds["token"]) > 20

    def test_credentials_client_id_format(self):
        """client_id should follow format: staff_{owner}_{bot_id}_{hex32}."""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {"agent_code": "ac-001"}
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_fmt", "bot_name": "F"},
            user_id="u042",
            machine_id="m-001",
        )

        payload = mocks["baas"].post_bots_api.call_args.kwargs["payload"]
        client_id = payload["config"]["deploy_config"]["credentials"]["client_id"]
        assert client_id.startswith("staff_u042_desktop_bot_fmt_")
        hex_suffix = client_id[-32:]
        assert len(hex_suffix) == 32
        int(hex_suffix, 16)  # should not raise

    def test_credentials_persisted_in_ext(self):
        """client_id and callback_token should be persisted in bot ext for restart recovery."""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {"agent_code": "ac-001"}
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_ext", "bot_name": "E"},
            user_id="u001",
            machine_id="m-001",
        )

        bot_insert_call = mocks["bot_repo"].insert.call_args[0][0]
        ext = bot_insert_call["ext"]
        assert "client_id" in ext
        assert "callback_token" in ext
        assert ext["client_id"].startswith("staff_u001_desktop_bot_ext_")
        assert len(ext["callback_token"]) > 20

    def test_credentials_ext_matches_deploy_config(self):
        """client_id and callback_token in ext should match those in deploy_config."""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {"agent_code": "ac-001"}
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001", "publish_id": 1}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_match", "bot_name": "M"},
            user_id="u001",
            machine_id="m-001",
        )

        payload = mocks["baas"].post_bots_api.call_args.kwargs["payload"]
        creds = payload["config"]["deploy_config"]["credentials"]
        ext = mocks["bot_repo"].insert.call_args[0][0]["ext"]

        assert ext["client_id"] == creds["client_id"]
        assert ext["callback_token"] == creds["token"]

    def test_credentials_empty_agent_code_defaults_to_empty_string(self):
        """When agent_code is empty, agent_code should be empty string, not None."""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {"agent_code": ""}
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_noac", "bot_name": "N"},
            user_id="u001",
            machine_id="m-001",
        )

        payload = mocks["baas"].post_bots_api.call_args.kwargs["payload"]
        creds = payload["config"]["deploy_config"]["credentials"]
        assert creds["agent_code"] == ""

    def test_credentials_unique_across_calls(self):
        """Each create call should generate unique client_id and callback_token."""
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {"agent_code": "ac-001"}
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bu-001"}
        mocks["binding_repo"].insert_binding.return_value = 1

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_a", "bot_name": "A"},
            user_id="u001",
            machine_id="m-001",
        )
        payload_a = mocks["baas"].post_bots_api.call_args.kwargs["payload"]
        creds_a = payload_a["config"]["deploy_config"]["credentials"]

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_b", "bot_name": "B"},
            user_id="u001",
            machine_id="m-001",
        )
        payload_b = mocks["baas"].post_bots_api.call_args.kwargs["payload"]
        creds_b = payload_b["config"]["deploy_config"]["credentials"]

        assert creds_a["client_id"] != creds_b["client_id"]
        assert creds_a["token"] != creds_b["token"]


class TestValidateMountPath:
    def test_none_is_valid(self):
        DesktopBotService._validate_mount_path(None)

    def test_empty_string_is_valid(self):
        DesktopBotService._validate_mount_path("")

    def test_whitespace_only_is_valid(self):
        DesktopBotService._validate_mount_path("   ")

    def test_absolute_path_is_valid(self):
        DesktopBotService._validate_mount_path("/home/admin/data")

    def test_relative_path_rejected(self):
        with pytest.raises(DesktopBotServiceError, match="must be absolute path"):
            DesktopBotService._validate_mount_path("home/admin/data")

    def test_tilde_path_rejected(self):
        with pytest.raises(DesktopBotServiceError, match="must be absolute path"):
            DesktopBotService._validate_mount_path("~/Desktop")

    def test_directory_traversal_rejected(self):
        with pytest.raises(DesktopBotServiceError, match="directory traversal"):
            DesktopBotService._validate_mount_path("/home/../etc/passwd")

    def test_system_dir_etc_rejected(self):
        with pytest.raises(DesktopBotServiceError, match="system directory"):
            DesktopBotService._validate_mount_path("/etc")

    def test_system_dir_root_rejected(self):
        with pytest.raises(DesktopBotServiceError, match="system directory"):
            DesktopBotService._validate_mount_path("/root")

    def test_system_dir_subpath_rejected(self):
        with pytest.raises(DesktopBotServiceError, match="system directory"):
            DesktopBotService._validate_mount_path("/etc/nginx")

    def test_normal_path_with_system_prefix_not_rejected(self):
        # /etc2 is NOT /etc — should be allowed
        DesktopBotService._validate_mount_path("/etc2/config")

    def test_create_validates_mount_path(self):
        service, mocks = _make_service_with_mocks()
        with pytest.raises(DesktopBotServiceError, match="must be absolute path"):
            service.apply_passport_before_create(
                bot={"bot_name": "B"}, user_id="u001", machine_id="m-001",
                mount_path="relative/path",
            )


class TestFetchMachineInfo:
    def test_returns_device_token(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "code": 0,
            "data": {
                "device_token": "tok-abc",
                "machine_id": "m-001",
                "machine_name": "MacBook-Pro",
            },
        }

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            result = service._fetch_machine_info("m-001")

        assert result["device_token"] == "tok-abc"
        assert result["machine_name"] == "MacBook-Pro"
        call_args = mock_client.return_value.__enter__.return_value.get.call_args
        assert "m-001" in call_args[0][0]
        # Tenant passed through from BaasConfig; neutral default after OSS-0 #3
        # (was the corp "team_claw" default).
        assert call_args[1]["params"]["tenant"] == "default"

    def test_api_error_raises(self):
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"code": 1, "message": "Not found"}

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            with pytest.raises(DesktopBotServiceError, match="BaaS API error: machine_id=m-001 message=Not found"):
                service._fetch_machine_info("m-001")


class TestRestart:
    def test_restart_success(self):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")

        mocks["baas"].restart_bot.return_value = {"publish_id": 5}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}

        result = service.restart(bot_id="desktop_bot_001", user_id="u001")

        assert result["device_id"] == "m-001"
        assert result["status"] == "PENDING"
        mocks["baas"].restart_bot.assert_called_once()
        mocks["baas"].approve_publish.assert_called_once()
        mocks["binding_repo"].update_status.assert_called_once()
        # update_by_owner 被调用两次:一次写 status=PENDING,一次经
        # _merge_bot_ext 写 ext.pending_since(供扫描超时兜底)。
        assert mocks["bot_repo"].update_by_owner.call_count >= 1

    def test_restart_stamps_pending_since(self):
        """重启写 PENDING 时,必须往 ext 记 pending_since,供扫描超时兜底使用。

        没有它,扫描的过渡超时兜底永远不触发(缺失=不超时),卡死的 bot
        无法被推进;但它又不能用 gmt_create(老 bot 会被误判)。
        """
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].restart_bot.return_value = {"publish_id": 5}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}

        service.restart(bot_id="desktop_bot_001", user_id="u001")

        # 找到写入 ext 且含 pending_since 的那次 update_by_owner 调用
        ext_writes = [
            c for c in mocks["bot_repo"].update_by_owner.call_args_list
            if "ext" in (c.kwargs.get("update_data") or {})
        ]
        assert ext_writes, "restart 应写一次含 ext 的 update"
        merged_ext = ext_writes[-1].kwargs["update_data"]["ext"]
        assert "pending_since" in merged_ext

    def test_restart_no_publish_id_skips_approve(self):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].restart_bot.return_value = {}

        service.restart(bot_id="desktop_bot_001", user_id="u001")

        mocks["baas"].approve_publish.assert_not_called()

    def test_restart_approve_failure_raises(self):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].restart_bot.return_value = {"publish_id": 5}
        mocks["baas"].approve_publish.side_effect = Exception("approve boom")

        with pytest.raises(DesktopBotServiceError, match="重启审批失败"):
            service.restart(bot_id="desktop_bot_001", user_id="u001")

    def test_restart_bot_not_found(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = None

        with pytest.raises(DesktopBotServiceError, match="bot not found"):
            service.restart(bot_id="desktop_bot_001", user_id="u001")

    def test_restart_binding_not_found(self):
        service, mocks = _make_service_with_mocks()
        bot = {"bot_id": "desktop_bot_001", "env": "dev", "binding_id": 1, "device_id": "m-001"}
        mocks["bot_repo"].get_by_id_and_owner.return_value = bot
        mocks["binding_repo"].get_by_id.return_value = None

        with pytest.raises(DesktopBotServiceError, match="binding not found"):
            service.restart(bot_id="desktop_bot_001", user_id="u001")


class TestVerifyOwnership:
    def test_verify_ownership_success(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = {"bot_id": "desktop_bot_001"}

        service.verify_ownership(bot_id="desktop_bot_001", user_id="staff_u001")

    def test_verify_ownership_bot_not_found(self):
        from agentclaw.community.core.errors import NotFound

        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = None

        with pytest.raises(NotFound, match="Bot not found"):
            service.verify_ownership(bot_id="desktop_bot_001", user_id="staff_u001")


class TestPublishPolling:
    """Tests for publish progress polling logic."""

    @pytest.fixture(autouse=True)
    def _instant_poll_interval(self, monkeypatch):
        """Drop ``_POLL_INTERVAL_SECONDS`` to zero for this class.

        ``_poll_publish_progress`` sleeps the interval *before* its first status
        query, so each of these tests paid the full 5s even though the very first
        mocked response is terminal — 20s of pure wall-clock across the class. The
        loop structure and exit conditions are unchanged by a zero interval; the
        four tests that assert on timeout behaviour drive ``time.monotonic``
        themselves and already patch ``time.sleep``, so they are unaffected.
        """
        monkeypatch.setattr(DesktopBotService, "_POLL_INTERVAL_SECONDS", 0)

    def test_poll_success_triggers_device_alive_instead_of_update_local_status(self):
        """SUCCESS 时应调 _trigger_device_alive(device_id)，不单独调 _update_local_status。"""
        service, mocks = _make_service_with_mocks()
        service._trigger_device_alive = MagicMock()

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {"code": 0, "data": {"status": "SUCCESS"}}
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response

            service._poll_publish_progress(
                publish_id="pub-001",
                binding_id="1",
                bot_id="desktop_bot_001",
                owner_id="u001",
                device_id="BOT-abc",
            )

        service._trigger_device_alive.assert_called_once_with("BOT-abc")
        # _update_local_status 不应再被单独调用（由 report_device_alive 内部处理）
        mocks["binding_repo"].update_status.assert_not_called()

    def test_poll_failed_still_calls_update_local_status(self):
        """FAILED 时走原有 _update_local_status 逻辑。"""
        service, mocks = _make_service_with_mocks()
        service._trigger_device_alive = MagicMock()

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {"code": 0, "data": {"status": "FAILED"}}
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response

            service._poll_publish_progress(
                publish_id="pub-001",
                binding_id="1",
                bot_id="desktop_bot_001",
                owner_id="u001",
                device_id="BOT-abc",
            )

        service._trigger_device_alive.assert_not_called()
        mocks["binding_repo"].update_status.assert_called_once_with(
            binding_id="1", status="FAILED",
        )

    def test_service_holds_device_service_reference(self):
        """DesktopBotService 应持有 device_service 属性。"""
        service, mocks = _make_service_with_mocks()
        assert hasattr(service, "_device_service")
        assert service._device_service is mocks["device_service"]

    def test_poll_success_updates_status_to_active(self):
        service, mocks = _make_service_with_mocks()
        service._trigger_device_alive = MagicMock()

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "code": 0,
                "data": {"status": "SUCCESS"},
            }
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            service._poll_publish_progress(
                publish_id="pub-001",
                binding_id="1",
                bot_id="desktop_bot_001",
                owner_id="u001",
                device_id="BOT-test",
            )

        service._trigger_device_alive.assert_called_once_with("BOT-test")
        # _update_local_status 不再被调用（由 report_device_alive 内部处理）
        mocks["binding_repo"].update_status.assert_not_called()

    def test_poll_failed_updates_status_to_failed(self):
        service, mocks = _make_service_with_mocks()

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "code": 0,
                "data": {"status": "FAILED"},
            }
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            service._poll_publish_progress(
                publish_id="pub-001",
                binding_id="1",
                bot_id="desktop_bot_001",
                owner_id="u001",
                device_id="BOT-test",
            )

        mocks["binding_repo"].update_status.assert_called_once_with(
            binding_id="1", status="FAILED",
        )

    @patch("time.sleep", return_value=None)
    @patch("time.monotonic")
    def test_poll_timeout_keeps_pending_and_sets_downloading(self, mock_monotonic, mock_sleep):
        """Poll timeout should keep PENDING + ext.start_status=DOWNLOADING, delegate to periodic scan."""
        service, mocks = _make_service_with_mocks()
        # Simulate time progression: start=0, then each call advances 10s
        # After 19 calls (190s > 180s timeout), loop exits
        call_count = [0]

        def advancing_time():
            val = call_count[0] * 10
            call_count[0] += 1
            return val

        mock_monotonic.side_effect = advancing_time
        mocks["bot_repo"].get_by_id_and_owner.return_value = {
            "bot_id": "desktop_bot_001",
            "ext": {},
        }

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "code": 0,
                "data": {"status": "RUNNING"},
            }
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            service._poll_publish_progress(
                publish_id="pub-001",
                binding_id="1",
                bot_id="desktop_bot_001",
                owner_id="u001",
                device_id="BOT-test",
            )

        # Poll timeout should NOT call _update_local_status (keep PENDING)
        mocks["binding_repo"].update_status.assert_not_called()
        # Should update ext.start_status=DOWNLOADING via bot_repo
        mocks["bot_repo"].get_by_id_and_owner.assert_called_once_with(
            bot_id="desktop_bot_001", owner_id="u001",
        )
        ext_update_call = mocks["bot_repo"].update_by_owner.call_args
        assert ext_update_call is not None
        update_data = ext_update_call.kwargs.get("update_data") or ext_update_call[1]
        assert update_data["ext"]["start_status"] == "DOWNLOADING"
        assert "start_message" in update_data["ext"]
        assert "下载" in update_data["ext"]["start_message"]

    @patch("time.sleep", return_value=None)
    @patch("time.monotonic")
    def test_poll_retries_on_query_error(self, mock_monotonic, mock_sleep):
        """Network errors during polling are retried, not treated as failure."""
        service, mocks = _make_service_with_mocks()
        service._trigger_device_alive = MagicMock()
        # time: 0, 5, 10 - within timeout
        mock_monotonic.side_effect = [0, 0, 5, 5, 10, 10]

        with patch("httpx.Client") as mock_client:
            mock_get = mock_client.return_value.__enter__.return_value.get

            error_response = MagicMock()
            error_response.raise_for_status.side_effect = Exception("Network error")

            success_response = MagicMock()
            success_response.raise_for_status.return_value = None
            success_response.json.return_value = {
                "code": 0,
                "data": {"status": "SUCCESS"},
            }

            mock_get.side_effect = [error_response, success_response]

            service._poll_publish_progress(
                publish_id="pub-001",
                binding_id="1",
                bot_id="desktop_bot_001",
                owner_id="u001",
                device_id="BOT-test",
            )

        service._trigger_device_alive.assert_called_once_with("BOT-test")
        # _update_local_status 不再被调用（由 report_device_alive 内部处理）
        mocks["binding_repo"].update_status.assert_not_called()

    @patch("time.sleep", return_value=None)
    @patch("time.monotonic")
    def test_poll_uses_extended_timeout_for_non_default_engine(self, mock_monotonic, mock_sleep):
        """非默认引擎(如 claude_code)使用更长的轮询超时(600s 而非 180s)。"""
        service, mocks = _make_service_with_mocks()
        # Simulate time progression: start=0, then each call advances 20s
        # After 10 calls (200s > 180s default timeout), loop should NOT exit
        # because extended timeout is 600s. After 31 calls (620s > 600s), it exits.
        call_count = [0]

        def advancing_time():
            val = call_count[0] * 20
            call_count[0] += 1
            return val

        mock_monotonic.side_effect = advancing_time
        mocks["bot_repo"].get_by_id_and_owner.return_value = {
            "bot_id": "desktop_bot_001",
            "ext": {},
        }

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "code": 0,
                "data": {"status": "RUNNING"},
            }
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            service._poll_publish_progress(
                publish_id="pub-001",
                binding_id="1",
                bot_id="desktop_bot_001",
                owner_id="u001",
                device_id="BOT-test",
                engine_type="claude_code",
            )

        # Should have timed out with extended timeout (600s), not default (180s)
        # Check that the loop ran more iterations than 180s/20s = 9 iterations
        assert call_count[0] > 10  # More than 200s worth of calls = past default timeout
        # Poll timeout should NOT call _update_local_status (keep PENDING)
        mocks["binding_repo"].update_status.assert_not_called()
        # Should update ext.start_status=DOWNLOADING
        ext_update_call = mocks["bot_repo"].update_by_owner.call_args
        assert ext_update_call is not None
        update_data = ext_update_call.kwargs.get("update_data") or ext_update_call[1]
        assert update_data["ext"]["start_status"] == "DOWNLOADING"

    def test_query_publish_status_returns_status(self):
        service = _make_service()

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "code": 0,
                "data": {"status": "ACTIVE", "id": 456},
            }
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            status = service._query_publish_status("pub-123")

        assert status == "ACTIVE"

    def test_query_publish_status_api_error_raises(self):
        service = _make_service()

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {"code": 1, "message": "Not found"}
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            with pytest.raises(DesktopBotServiceError, match="Publish progress API error"):
                service._query_publish_status("pub-123")

    @patch(
        "agentclaw.community.core.desktop_bot.services.desktop_bot_service.DesktopBotService._start_publish_polling"
    )
    def test_create_continue_starts_polling_when_publish_id_present(self, mock_start_poll):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {
            "bot_uuid": "bot-uuid-001", "publish_id": 42,
        }
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}
        mocks["binding_repo"].insert_binding.return_value = 10

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_001", "bot_name": "My Bot"},
            user_id="u001",
            machine_id="m-001",
        )

        mock_start_poll.assert_called_once_with(
            publish_id="42",
            binding_id="10",
            bot_id="desktop_bot_001",
            owner_id="u001",
            device_id="bot-uuid-001",
            engine_type="openclaw",
        )

    @patch(
        "agentclaw.community.core.desktop_bot.services.desktop_bot_service.DesktopBotService._start_publish_polling"
    )
    def test_create_continue_skips_polling_when_no_publish_id(self, mock_start_poll):
        service, mocks = _make_service_with_mocks()
        mocks["passport"].query_agent_passport.return_value = {
            "agent_code": "ac-001",
        }
        mocks["baas"].post_bots_api.return_value = {"bot_uuid": "bot-uuid-001"}
        mocks["binding_repo"].insert_binding.return_value = 10

        service.create_after_authorization(
            bot={"bot_id": "desktop_bot_001", "bot_name": "My Bot"},
            user_id="u001",
            machine_id="m-001",
        )

        mock_start_poll.assert_not_called()

    @patch(
        "agentclaw.community.core.desktop_bot.services.desktop_bot_service.DesktopBotService._start_publish_polling"
    )
    def test_restart_starts_polling_when_publish_id_present(self, mock_start_poll):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001")
        mocks["baas"].restart_bot.return_value = {"publish_id": 5}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}

        service.restart(bot_id="desktop_bot_001", user_id="u001")

        mock_start_poll.assert_called_once_with(
            publish_id="5",
            binding_id="1",
            bot_id="desktop_bot_001",
            owner_id="staff_u001",
            device_id="m-001",
            engine_type="openclaw",
        )

    @patch(
        "agentclaw.community.core.desktop_bot.services.desktop_bot_service.DesktopBotService._start_publish_polling"
    )
    def test_restart_skips_polling_when_no_publish_id(self, mock_start_poll):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].restart_bot.return_value = {}

        service.restart(bot_id="desktop_bot_001", user_id="u001")

        mock_start_poll.assert_not_called()

    @patch(
        "agentclaw.community.core.desktop_bot.services.desktop_bot_service.DesktopBotService._start_publish_polling"
    )
    def test_restart_passes_engine_type_to_polling(self, mock_start_poll):
        """重启时 active_engine 透传到 _start_publish_polling。"""
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", active_engine="claude_code")
        mocks["baas"].restart_bot.return_value = {"publish_id": 5}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}

        service.restart(bot_id="desktop_bot_001", user_id="u001")

        mock_start_poll.assert_called_once_with(
            publish_id="5",
            binding_id="1",
            bot_id="desktop_bot_001",
            owner_id="staff_u001",
            device_id="m-001",
            engine_type="claude_code",
        )


class TestDelete:
    def test_delete_success(self):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")

        mocks["baas"].destroy_bot.return_value = {"publish_id": 5}
        mocks["baas"].approve_publish.return_value = {"status": "SUCCESS"}

        result = service.delete(bot_id="desktop_bot_001", user_id="u001")

        assert result["status"] == "DELETED"
        mocks["baas"].destroy_bot.assert_called_once()
        mocks["baas"].approve_publish.assert_called_once()
        mocks["binding_repo"].release_binding.assert_called_once()
        mocks["bot_repo"].soft_delete_by_owner.assert_called_once()

    def test_delete_no_publish_id_skips_approve(self):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].destroy_bot.return_value = {}

        service.delete(bot_id="desktop_bot_001", user_id="u001")

        mocks["baas"].approve_publish.assert_not_called()

    def test_delete_approve_failure_does_not_block(self):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].destroy_bot.return_value = {"publish_id": 5}
        mocks["baas"].approve_publish.side_effect = Exception("approve boom")

        result = service.delete(bot_id="desktop_bot_001", user_id="u001")

        assert result["status"] == "DELETED"
        mocks["binding_repo"].release_binding.assert_called_once()

    def test_delete_local_cleanup_failure_does_not_raise(self):
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].destroy_bot.return_value = {}
        mocks["binding_repo"].release_binding.side_effect = Exception("DB down")

        result = service.delete(bot_id="desktop_bot_001", user_id="u001")

        assert result["status"] == "DELETED"

    def test_delete_baas_destroy_failure_still_cleans_local(self):
        """BaaS destroy 500 时不阻塞本地清理，用户数据照删。"""
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].destroy_bot.side_effect = Exception("BaaS 500")

        result = service.delete(bot_id="desktop_bot_001", user_id="u001")

        assert result["status"] == "DELETED"
        assert result["baas_destroy_ok"] is False
        mocks["binding_repo"].release_binding.assert_called_once()
        mocks["bot_repo"].soft_delete_by_owner.assert_called_once()

    def test_delete_passport_failure_does_not_block(self):
        """Passport 销毁失败时不阻塞本地清理。"""
        service, mocks = _make_service_with_mocks()
        _setup_local_lookup(mocks, bot_id="desktop_bot_001", device_id="m-001")
        mocks["baas"].destroy_bot.return_value = {}
        mocks["passport"].destroy_passport.side_effect = PassportError("passport boom")

        result = service.delete(bot_id="desktop_bot_001", user_id="u001")

        assert result["status"] == "DELETED"
        assert result["baas_destroy_ok"] is True
        mocks["binding_repo"].release_binding.assert_called_once()
        mocks["bot_repo"].soft_delete_by_owner.assert_called_once()


class TestOpenFolder:
    def test_success(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = {
            "bot_id": "b1",
            "device_id": "bot-uuid-001",
            "ext": {},
        }
        mocks["baas"].open_folder_bot.return_value = {}

        result = service.open_folder(bot_id="b1", user_id="u001")

        assert result == {"bot_id": "b1"}
        mocks["bot_repo"].get_by_id_and_owner.assert_called_once_with(
            bot_id="b1", owner_id="u001",
        )
        mocks["baas"].open_folder_bot.assert_called_once_with(
            bot_uuid="bot-uuid-001", folder_path=None,
        )

    def test_folder_path_passed_through(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = {
            "bot_id": "b1",
            "device_id": "bot-uuid-001",
            "ext": {},
        }
        mocks["baas"].open_folder_bot.return_value = {}

        service.open_folder(bot_id="b1", user_id="u001", folder_path="src/components")

        mocks["baas"].open_folder_bot.assert_called_once_with(
            bot_uuid="bot-uuid-001", folder_path="src/components",
        )

    def test_bot_not_found_raises(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = None

        with pytest.raises(DesktopBotServiceError, match="Desktop bot not found"):
            service.open_folder(bot_id="b1", user_id="u001")

    def test_no_device_id_raises(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = {
            "bot_id": "b1",
            "ext": {},
        }

        with pytest.raises(DesktopBotServiceError, match="has no device_id"):
            service.open_folder(bot_id="b1", user_id="u001")

    def test_device_id_empty_raises(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = {
            "bot_id": "b1",
            "device_id": "",
            "ext": {},
        }

        with pytest.raises(DesktopBotServiceError, match="has no device_id"):
            service.open_folder(bot_id="b1", user_id="u001")

    def test_baas_error_raises(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].get_by_id_and_owner.return_value = {
            "bot_id": "b1",
            "device_id": "bot-uuid-001",
            "ext": {},
        }
        mocks["baas"].open_folder_bot.side_effect = BaasServiceError(
            "folder not found"
        )

        with pytest.raises(DesktopBotServiceError, match="Failed to open folder"):
            service.open_folder(bot_id="b1", user_id="u001")


# ── helpers ──────────────────────────────────────────────────────────────


DESKTOP_TEMPLATE_UUID = "desk-tpl-001"


def _make_service():
    """Create a DesktopBotService with all-mock dependencies."""
    from agentclaw.community.di.config import BaasConfig
    from agentclaw.community.core.devices.services.device_service import DeviceService
    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes.return_value = []
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value = skill_set_service
    return DesktopBotService(
        baas_service=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        bot_repository=MagicMock(),
        baas_config=BaasConfig(desktop_template_uuid=DESKTOP_TEMPLATE_UUID),
        device_service=MagicMock(spec=DeviceService),
        skill_set_factory=skill_set_factory,
    )


def _make_service_with_mocks():
    """Create a DesktopBotService with accessible mock dependencies."""
    from agentclaw.community.di.config import BaasConfig
    from agentclaw.community.core.devices.services.device_service import DeviceService
    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes.return_value = []
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value = skill_set_service
    mocks = {
        "baas": MagicMock(),
        "passport": MagicMock(),
        "binding_repo": MagicMock(),
        "bot_repo": MagicMock(),
        "device_service": MagicMock(spec=DeviceService),
        "skill_set_factory": skill_set_factory,
    }
    service = DesktopBotService(
        baas_service=mocks["baas"],
        passport_plugin=mocks["passport"],
        device_binding_repo=mocks["binding_repo"],
        bot_repository=mocks["bot_repo"],
        baas_config=BaasConfig(desktop_template_uuid=DESKTOP_TEMPLATE_UUID),
        device_service=mocks["device_service"],
        skill_set_factory=mocks["skill_set_factory"],
    )
    # _fetch_machine_info makes a real HTTP call; mock it for all unit tests
    service._fetch_machine_info = MagicMock(
        return_value={"device_token": "dev-tok-001", "machine_name": "mock-pc"}
    )
    # _build_desktop_bot_payload calls _get_start_cmd and _get_destroy_cmd on baas
    mocks["baas"]._get_start_cmd.return_value = "echo start"
    mocks["baas"]._get_destroy_cmd.return_value = None
    return service, mocks


def _setup_local_lookup(mocks, bot_id, device_id="m-001", active_engine="openclaw"):
    """Set up the mocks for _lookup_local success case."""
    binding = MagicMock(id=1, entity_id="staff_u001", entity_type="staff")
    bot = {"bot_id": bot_id, "env": "dev", "binding_id": 1, "device_id": device_id, "active_engine": active_engine}
    mocks["bot_repo"].get_by_id_and_owner.return_value = bot
    mocks["binding_repo"].get_by_id.return_value = binding


class TestListUserBots:
    def test_returns_bots_for_user(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].search_bots.side_effect = [
            (1, [{"bot_id": "b1", "status": "PENDING"}]),
            (1, [{"bot_id": "b2", "status": "ACTIVE"}]),
            (1, [{"bot_id": "b3", "status": "OFFLINE"}]),
            (0, []),  # RELEASING
            (0, []),  # FAILED
        ]

        result = service.list_user_bots(user_id="u001")

        assert len(result) == 3
        assert result[0]["bot_id"] == "b1"
        assert result[1]["bot_id"] == "b2"
        assert result[2]["bot_id"] == "b3"
        # Called for PENDING, ACTIVE, OFFLINE, RELEASING, FAILED
        assert mocks["bot_repo"].search_bots.call_count == 5

    def test_returns_empty_when_no_bots(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].search_bots.return_value = (0, [])

        result = service.list_user_bots(user_id="u001")

        assert result == []

    def test_passes_owner_id_filter(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].search_bots.return_value = (0, [])

        service.list_user_bots(user_id="u001")

        for call in mocks["bot_repo"].search_bots.call_args_list:
            assert call.kwargs["owner_id"] == "u001"
            assert call.kwargs["bot_type"] == "desktop"

    def test_continues_on_query_failure(self):
        service, mocks = _make_service_with_mocks()
        mocks["bot_repo"].search_bots.side_effect = [
            Exception("DB error"),
            (1, [{"bot_id": "b1", "status": "ACTIVE"}]),
            (0, []),
            (0, []),
            (0, []),
        ]

        result = service.list_user_bots(user_id="u001")

        assert len(result) == 1
