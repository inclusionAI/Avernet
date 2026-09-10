"""Tests for MCPConfigService."""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.di.config import McpRuntimeCredentialsConfig


def _service(*, repo=None, center=None, credentials=None, resolver=None):
    return MCPConfigService(
        user_mcp_config_repo=repo or MagicMock(),
        mcp_center=center or MagicMock(),
        bot_repo=MagicMock(),
        mcp_runtime_credentials=credentials or McpRuntimeCredentialsConfig(),
        secret_resolver=resolver or MagicMock(),
    )


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
        svc = _service(repo=repo)
        result = svc.get_user_unified_config("user1", "mcp.test")
        assert result["api_key"] == "secret"
        assert result["headers"] == {"x-foo": "bar"}
        assert result["endpoint_env"] == "PRE"
        assert result["transport_protocol"] == "SSE"

    def test_get_config_returns_none_when_not_found(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = None
        svc = _service(repo=repo)
        assert svc.get_user_unified_config("user1", "mcp.test") is None

    def test_get_config_parses_json_string_extra_config(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {
            "id": "1",
            "extra_config": '{"api_key": "key2", "headers": {}, "endpoint_env": "PROD"}',
        }
        svc = _service(repo=repo)
        result = svc.get_user_unified_config("user1", "mcp.test")
        assert result["api_key"] == "key2"


class TestMCPConfigServiceValidateHeaders:
    """Tests for validate_headers_for_mcp."""

    def test_validate_headers_success(self):
        center = MagicMock()
        center.get_mcp_detail.return_value = {"serverCode": "mcp.test"}
        svc = _service(center=center)
        result = svc.validate_headers_for_mcp("mcp.test", {"x-foo": "bar"})
        assert result["valid"] is True

    def test_validate_headers_server_not_found(self):
        center = MagicMock()
        center.get_mcp_detail.return_value = None
        svc = _service(center=center)
        result = svc.validate_headers_for_mcp("mcp.test", {"x-foo": "bar"})
        assert result["valid"] is False

    def test_validate_headers_empty_key(self):
        center = MagicMock()
        center.get_mcp_detail.return_value = {"serverCode": "mcp.test"}
        svc = _service(center=center)
        result = svc.validate_headers_for_mcp("mcp.test", {"": "bar"})
        assert result["valid"] is False


class TestMCPConfigServiceUpdateConfig:
    """Tests for update_user_unified_config and rollback_unified_config."""

    def test_update_user_unified_config_creates_new(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = None
        repo.create.return_value = {"id": "1"}

        svc = _service(repo=repo)
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

        svc = _service(repo=repo)
        svc.update_user_unified_config(
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

        svc = _service(repo=repo)
        svc.rollback_unified_config(
            user_id="user1",
            server_code="mcp.test",
            old_config=None,
        )
        repo.delete.assert_called_once_with("1")

    def test_rollback_unified_config_restores_old(self):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {"id": "1"}

        svc = _service(repo=repo)
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

        svc = _service(repo=repo)

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

        svc = _service(repo=repo)

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

    def test_managed_header_overrides_user_header_case_insensitively(self, monkeypatch):
        repo = MagicMock()
        repo.get_by_user_and_server_code.return_value = {
            "extra_config": {"headers": {"X-Ling-Auth": "user-value"}}
        }
        resolver = MagicMock()
        resolver.get_secret.return_value = SimpleNamespace(secret_value="managed-value")
        svc = _service(
            repo=repo,
            credentials=McpRuntimeCredentialsConfig(
                header_secrets={"mcp.test": {"x-ling-auth": "test-secret"}}
            ),
            resolver=resolver,
        )
        monkeypatch.setattr(
            "agentclaw.community.core.mcp.services._defaults.get_default_mcp_servers",
            lambda _engine: [
                {"server_code": "mcp.test", "headers": {"x-default": "1"}}
            ],
        )

        _, headers, _, _ = svc.build_mcp_sync_payload(
            user_id="user1", mcp_data={"serverCode": "mcp.test"}
        )

        assert headers == {"x-default": "1", "x-ling-auth": "managed-value"}

    def test_managed_header_success_is_cached_for_process_lifetime(self):
        resolver = MagicMock()
        resolver.get_secret.return_value = SimpleNamespace(secret_value="managed-value")
        svc = _service(
            credentials=McpRuntimeCredentialsConfig(
                header_secrets={"mcp.test": {"authorization": "shared-secret"}}
            ),
            resolver=resolver,
        )

        for _ in range(2):
            _, headers, _, _ = svc.build_mcp_sync_payload(
                user_id="user1", mcp_data={"serverCode": "mcp.test"}
            )

        assert headers["authorization"] == "managed-value"
        resolver.get_secret.assert_called_once_with("shared-secret")

    def test_managed_header_failure_is_not_cached(self):
        resolver = MagicMock()
        resolver.get_secret.side_effect = [
            None,
            SimpleNamespace(secret_value="recovered-value"),
        ]
        svc = _service(
            credentials=McpRuntimeCredentialsConfig(
                header_secrets={"mcp.test": {"authorization": "test-secret"}}
            ),
            resolver=resolver,
        )

        with pytest.raises(RuntimeError, match="managed MCP Header secret is unavailable"):
            svc.build_mcp_sync_payload(
                user_id="user1", mcp_data={"serverCode": "mcp.test"}
            )

        _, headers, _, _ = svc.build_mcp_sync_payload(
            user_id="user1", mcp_data={"serverCode": "mcp.test"}
        )
        assert headers["authorization"] == "recovered-value"
        assert resolver.get_secret.call_count == 2

    def test_unmapped_server_does_not_read_secret_backend(self):
        resolver = MagicMock()
        svc = _service(
            credentials=McpRuntimeCredentialsConfig(
                header_secrets={"mcp.other": {"authorization": "test-secret"}}
            ),
            resolver=resolver,
        )

        _, headers, _, _ = svc.build_mcp_sync_payload(
            user_id="user1", mcp_data={"serverCode": "mcp.test"}
        )

        assert headers == {}
        resolver.get_secret.assert_not_called()
