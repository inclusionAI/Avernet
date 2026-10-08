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


def _mock_get_response(json_body: dict, status: int = 200) -> Mock:
    m = Mock()
    m.status_code = status
    m.json.return_value = json_body
    return m


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

    # ---- check_admission ----

    def test_check_admission_success_returns_json(self):
        """check_admission returns the BCS AdmissionResult dict on 200."""
        service, http = _make()
        http.set_response("get", _mock_get_response({
            "allowed": True,
            "grants": [{"kind": "permission_profile", "ref_id": "pp_bot_default", "source": "edge_grant"}],
            "reason_code": "ok",
            "public_default": False,
        }))

        result = service.check_admission(
            bot_uuid="20260421_gfdsz5vi:85020",
            actor="human_88001",
        )

        assert result["allowed"] is True
        assert result["reason_code"] == "ok"
        assert result["public_default"] is False
        assert len(result["grants"]) == 1
        assert result["grants"][0]["source"] == "edge_grant"

        call = http.calls_to("get")[0]
        assert call.args[0] == "/bots/20260421_gfdsz5vi:85020/admission"
        assert call.kwargs["params"] == {"actor": "human_88001"}

    def test_check_admission_with_originator(self):
        """check_admission passes originator param when provided."""
        service, http = _make()
        http.set_response("get", _mock_get_response({
            "allowed": False, "grants": [], "reason_code": "no_edge", "public_default": False,
        }))

        service.check_admission(
            bot_uuid="bot_1:85020",
            actor="human_88001",
            originator="human_85020",
        )

        call = http.calls_to("get")[0]
        assert call.kwargs["params"] == {"actor": "human_88001", "originator": "human_85020"}

    def test_check_admission_without_originator_omits_param(self):
        """check_admission omits originator when None."""
        service, http = _make()
        http.set_response("get", _mock_get_response({
            "allowed": True, "grants": [], "reason_code": "public_default", "public_default": True,
        }))

        service.check_admission(bot_uuid="bot_1:85020", actor="human_88001")

        call = http.calls_to("get")[0]
        assert "originator" not in call.kwargs["params"]
        assert call.kwargs["params"] == {"actor": "human_88001"}

    def test_check_admission_http_error_raises_bcs_error(self):
        """check_admission raises BcnServiceError on HTTP 500."""
        service, http = _make()
        error_response = Mock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=Mock(), response=error_response
        )
        http.set_response("get", error_response)

        with pytest.raises(BcnServiceError, match="BCS admission HTTP error: 500"):
            service.check_admission(bot_uuid="bot_1:85020", actor="human_88001")

    def test_check_admission_timeout_raises_bcs_error(self):
        """check_admission raises BcnServiceError on timeout."""
        service, http = _make()
        timeout_response = Mock()
        timeout_response.status_code = 200
        timeout_response.json.side_effect = httpx.TimeoutException("timeout")
        http.set_response("get", timeout_response)

        with pytest.raises(BcnServiceError, match="BCS admission"):
            service.check_admission(bot_uuid="bot_1:85020", actor="human_88001")

    # ---- onboard_bot ----

    def test_onboard_bot_success(self):
        """Test successful onboard_bot call."""
        service, http = _make()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "bot_uuid": "test-bot-uuid",
            "onboarded": True,
        }
        http.set_response("post", mock_response)

        result = service.onboard_bot(
            bot_id="20260421_gfdsz5vi:85020",
            name="Test Bot",
            summary="Test Summary",
        )

        assert result["onboarded"] is True

        call = http.calls_to("post")[0]
        assert call.args[0] == "/admin/bots/onboard"
        assert call.kwargs["json"]["bot_id"] == "20260421_gfdsz5vi:85020"

    def test_onboard_bot_bot_id_format(self):
        """Test onboard_bot with expected composite bot_id format."""
        service, http = _make()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "bot_uuid": "test-bot-uuid",
            "onboarded": True,
        }
        http.set_response("post", mock_response)

        result = service.onboard_bot(
            bot_id="20260421_gfdsz5vi:85020",
            name="Test",
            summary="Summary",
        )

        assert result["onboarded"] is True

        call = http.calls_to("post")[0]
        assert call.kwargs["json"]["bot_id"] == "20260421_gfdsz5vi:85020"