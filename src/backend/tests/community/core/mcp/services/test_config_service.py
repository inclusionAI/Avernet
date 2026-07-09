"""Tests for MCPConfigService."""
import pytest
from unittest.mock import MagicMock

from agentclaw.community.core.mcp.services.config_service import MCPConfigService


class TestMCPConfigServiceGetConfig:
    """Tests for get_user_unified_config."""

    def test_get_config_returns_parsed_values(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {
            "id": "1",
            "extra_config": {
                "api_key": "secret",
                "headers": {"x-foo": "bar"},
                "endpoint_env": "PRE",
                "transport_protocol": "SSE",
            },
        }
        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )
        result = svc.get_user_unified_config("user1", "mcp.test")
        assert result["api_key"] == "secret"
        assert result["headers"] == {"x-foo": "bar"}
        assert result["endpoint_env"] == "PRE"
        assert result["transport_protocol"] == "SSE"

    def test_get_config_returns_none_when_not_found(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = None
        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )
        assert svc.get_user_unified_config("user1", "mcp.test") is None

    def test_get_config_parses_json_string_extra_config(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {
            "id": "1",
            "extra_config": '{"api_key": "key2", "headers": {}, "endpoint_env": "PROD"}',
        }
        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )
        result = svc.get_user_unified_config("user1", "mcp.test")
        assert result["api_key"] == "key2"


class TestMCPConfigServiceValidateHeaders:
    """Tests for validate_headers_for_mcp."""

    def test_validate_headers_success(self):
        center = MagicMock()
        center.get_mcp_detail.return_value = {"serverCode": "mcp.test"}
        svc = MCPConfigService(
            user_mcp_config_repo=MagicMock(),
            mcp_center=center,
            bot_repo=MagicMock(),
        )
        result = svc.validate_headers_for_mcp("mcp.test", {"x-foo": "bar"})
        assert result["valid"] is True

    def test_validate_headers_server_not_found(self):
        center = MagicMock()
        center.get_mcp_detail.return_value = None
        svc = MCPConfigService(
            user_mcp_config_repo=MagicMock(),
            mcp_center=center,
            bot_repo=MagicMock(),
        )
        result = svc.validate_headers_for_mcp("mcp.test", {"x-foo": "bar"})
        assert result["valid"] is False

    def test_validate_headers_empty_key(self):
        center = MagicMock()
        center.get_mcp_detail.return_value = {"serverCode": "mcp.test"}
        svc = MCPConfigService(
            user_mcp_config_repo=MagicMock(),
            mcp_center=center,
            bot_repo=MagicMock(),
        )
        result = svc.validate_headers_for_mcp("mcp.test", {"": "bar"})
        assert result["valid"] is False


class TestMCPConfigServiceUpdateConfig:
    """Tests for update_user_unified_config and rollback_unified_config."""

    def test_update_user_unified_config_creates_new(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = None
        repo.create.return_value = {"id": "1"}

        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )
        old = svc.update_user_unified_config(
            user_id="user1",
            server_code="mcp.test",
            api_key="new-key",
            headers={"x-foo": "bar"},
            endpoint_env="PROD",
            transport_protocol="SSE",
        )
        assert old is None
        repo.create.assert_called_once()

    def test_update_user_unified_config_updates_existing(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {"id": "1"}

        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )
        old = svc.update_user_unified_config(
            user_id="user1",
            server_code="mcp.test",
            api_key="new-key",
            headers=None,
            endpoint_env=None,
            transport_protocol=None,
        )
        # old is None because get_user_unified_config returns None (mock default)
        repo.update.assert_called_once()

    def test_rollback_unified_config_deletes_when_old_is_none(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {"id": "1"}

        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )
        svc.rollback_unified_config(
            user_id="user1",
            server_code="mcp.test",
            old_config=None,
        )
        repo.delete.assert_called_once_with("1")

    def test_rollback_unified_config_restores_old(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {"id": "1"}

        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )
        svc.rollback_unified_config(
            user_id="user1",
            server_code="mcp.test",
            old_config={
                "api_key": "old-key",
                "headers": {"x-old": "val"},
                "endpoint_env": "PRE",
                "transport_protocol": "SSE",
            },
        )
        repo.update.assert_called_once()


class TestMCPConfigServiceBuildPayload:
    """Tests for build_mcp_sync_payload."""

    def test_build_payload_with_db_config(self, monkeypatch):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {
            "extra_config": {
                "api_key": "db-key",
                "headers": {"x-db": "1"},
                "endpoint_env": "PRE",
                "transport_protocol": "STREAMABLE_HTTP",
            }
        }

        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )

        monkeypatch.setattr(
            "agentclaw.community.core.mcp.services._defaults.get_default_mcp_servers",
            lambda _engine: [{"server_code": "mcp.test", "headers": {"x-default": "2"}}],
        )

        api_key, headers, endpoint_env, transport_protocol = svc.build_mcp_sync_payload(
            user_id="user1",
            mcp_data={"serverCode": "mcp.test"},
        )
        assert api_key == "db-key"
        assert headers == {"x-default": "2", "x-db": "1"}
        assert endpoint_env == "PRE"
        assert transport_protocol == "STREAMABLE_HTTP"

    def test_build_payload_without_db_config(self, monkeypatch):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = None

        svc = MCPConfigService(
            user_mcp_config_repo=repo,
            mcp_center=MagicMock(),
            bot_repo=MagicMock(),
        )

        monkeypatch.setattr(
            "agentclaw.community.core.mcp.services._defaults.get_default_mcp_servers",
            lambda _engine: [{"server_code": "mcp.test", "headers": {"x-default": "2"}}],
        )

        api_key, headers, endpoint_env, transport_protocol = svc.build_mcp_sync_payload(
            user_id="user1",
            mcp_data={"serverCode": "mcp.test"},
        )
        assert api_key is None
        assert headers == {"x-default": "2"}
        assert endpoint_env == "PROD"
        assert transport_protocol is None
