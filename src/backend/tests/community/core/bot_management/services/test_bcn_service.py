"""Tests for BcnService.

Tests the BCN service integration for syncing bot name and summary to BCN.
BcnService talks to BCN through an injected :class:`HttpClient`; tests drive it
with a :class:`LocalHttpClient` (stub the response, assert the call).
"""

import pytest
from unittest.mock import Mock
import httpx

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnService,
    BcnServiceError,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def _make(base_url: str = "http://test-bcn:21000") -> tuple[BcnService, LocalHttpClient]:
    http = LocalHttpClient(base_url=base_url)
    return BcnService(http_client=http), http


class TestBcnService:
    """Tests for BcnService class."""

    def test_init_stores_http_client_and_default_timeout(self):
        """Test initialization stores the injected client + default timeout."""
        http = LocalHttpClient()
        service = BcnService(http_client=http)
        assert service._http is http
        assert service._timeout == 30.0

    def test_init_with_custom_timeout(self):
        """Test initialization with a custom timeout."""
        service = BcnService(http_client=LocalHttpClient(), timeout=60.0)
        assert service._timeout == 60.0

    def test_onboard_bot_success(self):
        """Test successful onboard_bot call."""
        service, http = _make()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "bot_uuid": "test-bot-uuid",
            "onboarded": True,
            "name": "Test Bot",
        }
        http.set_response("post", mock_response)

        result = service.onboard_bot(
            bot_id="20260421_gfdsz5vi:85020",
            name="Test Bot",
            summary="Test Summary",
        )

        assert result["bot_uuid"] == "test-bot-uuid"
        assert result["onboarded"] is True
        assert result["name"] == "Test Bot"

        # Verify the request was made correctly (relative path; no auth headers)
        call = http.calls_to("post")[0]
        assert call.args[0] == "/admin/bots/onboard"
        assert call.kwargs["json"] == {
            "bot_id": "20260421_gfdsz5vi:85020",
            "name": "Test Bot",
            "summary": "Test Summary",
        }
        assert call.kwargs["headers"] is None

    def test_onboard_bot_with_hidden(self):
        """Test onboard_bot with hidden=True."""
        service, http = _make()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "bot_uuid": "test-bot-uuid",
            "onboarded": True,
        }
        http.set_response("post", mock_response)

        result = service.onboard_bot(
            bot_id="test-bot:12345",
            name="Hidden Bot",
            summary="Hidden Summary",
            hidden=True,
        )

        assert result["onboarded"] is True

        # Verify hidden field is included
        call = http.calls_to("post")[0]
        assert call.kwargs["json"]["hidden"] is True

    def test_onboard_bot_selects_auth_headers(self):
        """Test onboard_bot selects Cookie and Authorization from request headers."""
        service, http = _make()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "bot_uuid": "test-bot-uuid",
            "onboarded": True,
        }
        http.set_response("post", mock_response)

        request_headers = {
            "cookie": "IAM_TOKEN=test-token; other=value",
            "authorization": "Bearer opaque-or-jwt-token",
            "x-ignore-me": "ignored",
        }

        service.onboard_bot(
            bot_id="test-bot:12345",
            name="Test Bot",
            summary="Test Summary",
            request_headers=request_headers,
        )

        call = http.calls_to("post")[0]
        assert call.kwargs["headers"] == {
            "Cookie": "IAM_TOKEN=test-token; other=value",
            "Authorization": "Bearer opaque-or-jwt-token",
        }

    def test_onboard_bot_http_error(self):
        """Test onboard_bot handles HTTP errors."""
        service, http = _make()

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server error", request=Mock(), response=mock_response
        )
        http.set_response("post", mock_response)

        with pytest.raises(BcnServiceError) as exc_info:
            service.onboard_bot(
                bot_id="test-bot:12345",
                name="Test Bot",
                summary="Test Summary",
            )

        assert "HTTP error" in str(exc_info.value)

    def test_onboard_bot_error_response(self):
        """Test onboard_bot handles error response from BCN."""
        service, http = _make()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "error": "Bot not found",
        }
        http.set_response("post", mock_response)

        with pytest.raises(BcnServiceError) as exc_info:
            service.onboard_bot(
                bot_id="test-bot:12345",
                name="Test Bot",
                summary="Test Summary",
            )

        assert "Bot not found" in str(exc_info.value)

    def test_onboard_bot_timeout(self):
        """Test onboard_bot handles timeout."""
        service, http = _make()

        def _timeout(*_args, **_kwargs):
            raise httpx.TimeoutException("Request timed out")
        http.set_override("post", _timeout)

        with pytest.raises(BcnServiceError) as exc_info:
            service.onboard_bot(
                bot_id="test-bot:12345",
                name="Test Bot",
                summary="Test Summary",
            )

        assert "timeout" in str(exc_info.value).lower()


class TestBcnServiceBotIdFormat:
    """Tests for BCN bot_id format.

    BCN bot_id format: {tc_bot_id}:{owner_workno}
    Example: "20260421_gfdsz5vi:85020"
    """

    def test_bot_id_format_with_colon(self):
        """Test that bot_id with colon is correctly passed to BCN."""
        service, http = _make()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "bot_uuid": "test-bot-uuid",
            "onboarded": True,
        }
        http.set_response("post", mock_response)

        # Test with expected bot_id format: {tc_bot_id}:{owner_workno}
        result = service.onboard_bot(
            bot_id="20260421_gfdsz5vi:85020",
            name="SBTI超绝人格测试",
            summary="SBTI人格测试",
        )

        assert result["onboarded"] is True

        # Verify bot_id is passed as-is
        call = http.calls_to("post")[0]
        assert call.kwargs["json"]["bot_id"] == "20260421_gfdsz5vi:85020"
