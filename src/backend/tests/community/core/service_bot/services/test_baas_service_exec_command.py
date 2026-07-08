"""Tests for ``BaasService.exec_command_on_bot``."""
from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import BaasService, BaasServiceError


def _make_service(http_client=None) -> tuple[BaasService, MagicMock]:
    mock_http = http_client or MagicMock()
    svc = BaasService(
        baas_api_base="http://baas-test:8080",
        tenant="test_tenant",
        template_uuid="legacy-uuid",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=mock_http,
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
        personal_bot_template_uuid="TEMPLATE-test",
    )
    return svc, mock_http


class TestExecCommandOnBot:
    """Unit tests for exec_command_on_bot."""

    def test_success_returns_data(self):
        svc, mock_http = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "data": {
                "exit_code": 0,
                "stdout": "hello\n",
                "stderr": "",
                "execution_time_ms": 42,
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_http.post.return_value = mock_response

        result = svc.exec_command_on_bot(
            bot_uuid="BOT-123",
            cmd="echo hello",
        )

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello\n"

    def test_url_includes_tenant_and_bot_uuid(self):
        svc, mock_http = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 0, "data": {}}
        mock_response.raise_for_status = MagicMock()
        mock_http.post.return_value = mock_response

        svc.exec_command_on_bot(bot_uuid="BOT-ABC", cmd="ls")

        call_args = mock_http.post.call_args
        url = call_args[0][0]
        assert url == "/api/v1/bots/test_tenant/BOT-ABC/execute-command"

    def test_payload_includes_cmd_and_timeout(self):
        svc, mock_http = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 0, "data": {}}
        mock_response.raise_for_status = MagicMock()
        mock_http.post.return_value = mock_response

        svc.exec_command_on_bot(
            bot_uuid="BOT-1",
            cmd="bash /home/admin/bin/bootstrap_minimal.sh",
            timeout_seconds=60,
        )

        call_args = mock_http.post.call_args
        body = call_args[1]["json"]
        assert body["cmd"] == "bash /home/admin/bin/bootstrap_minimal.sh"
        assert body["timeout_seconds"] == 60
        assert "env" not in body

    def test_payload_includes_env_when_provided(self):
        svc, mock_http = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 0, "data": {}}
        mock_response.raise_for_status = MagicMock()
        mock_http.post.return_value = mock_response

        svc.exec_command_on_bot(
            bot_uuid="BOT-1",
            cmd="env",
            env={"FOO": "bar"},
        )

        body = mock_http.post.call_args[1]["json"]
        assert body["env"] == {"FOO": "bar"}

    def test_baas_api_error_raises(self):
        svc, mock_http = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 1,
            "message": "command timed out",
        }
        mock_response.raise_for_status = MagicMock()
        mock_http.post.return_value = mock_response

        with pytest.raises(BaasServiceError, match="command timed out"):
            svc.exec_command_on_bot(bot_uuid="BOT-1", cmd="sleep 999")

    def test_http_error_raises(self):
        svc, mock_http = _make_service()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        http_error = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=mock_response,
        )
        mock_response.raise_for_status.side_effect = http_error
        mock_http.post.return_value = mock_response

        with pytest.raises(BaasServiceError, match="500"):
            svc.exec_command_on_bot(bot_uuid="BOT-1", cmd="echo hi")

    def test_httpx_timeout_uses_cmd_timeout_plus_buffer(self):
        svc, mock_http = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 0, "data": {}}
        mock_response.raise_for_status = MagicMock()
        mock_http.post.return_value = mock_response

        svc.exec_command_on_bot(bot_uuid="BOT-1", cmd="ls", timeout_seconds=120)

        call_kwargs = mock_http.post.call_args[1]
        assert call_kwargs["timeout"] == 130.0

    def test_unexpected_exception_wraps_in_baas_error(self):
        svc, mock_http = _make_service()
        mock_http.post.side_effect = RuntimeError("network down")

        with pytest.raises(BaasServiceError, match="network down"):
            svc.exec_command_on_bot(bot_uuid="BOT-1", cmd="echo hi")
