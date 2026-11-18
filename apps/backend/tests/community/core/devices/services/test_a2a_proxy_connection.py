"""Credentialed A2A is optional, caller-gated, and shares only its own target."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.a2a_proxy_connection import usable_proxy_face
from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceService


BASE = "https://proxy.example/proxypass/ARCA_sandbox@0:20003"


def _material():
    return dict(base=BASE, target="ARCA_sandbox@0:20003", token="test-token",
                expires_at="2099-01-01T00:00:00Z")


@pytest.mark.parametrize("changes", [
    {"base": "http://proxy.example/proxypass/ARCA_sandbox@0:20003"},
    {"base": BASE + "?token=other"}, {"base": BASE + "#fragment"},
    {"base": BASE.replace("https://", "https://user:secret@")},
    {"base": "not-a-url"}, {"base": "https://[invalid"},
    {"target": "ARCA_other:20003"}, {"target": "TECLAW_sandbox:20003"},
    {"target": "ARCA_a/../b:20003"},
    {"token": ""}, {"token": "token\r\nCookie: secret"},
    {"expires_at": "2000-01-01T00:00:00Z"}, {"expires_at": ""},
    {"expires_at": "2099-01-01T00:00:00"}, {"expires_at": "broken"},
])
def test_invalid_optional_face_is_rejected(changes):
    assert not usable_proxy_face(**(_material() | changes))


def test_matching_https_target_with_live_credential_is_usable():
    assert usable_proxy_face(**_material())


def _service(*, allowed=True, engine="openclaw", bot_type="personal", **info_changes):
    svc = object.__new__(BaasDeviceService)
    svc._repo = MagicMock()
    svc._repo.get_by_id.return_value = None
    svc._default_engine = "openclaw"
    svc._bot_query = MagicMock()
    svc._bot_query.get_by_binding_id.return_value = {
        "bot_type": bot_type, "active_engine": engine,
    }
    svc._baas_service = MagicMock()
    svc._baas_service.get_ws_info.return_value = SimpleNamespace(**({
        "ws_url": "wss://proxy.example/proxypass/ARCA_sandbox@0:20003/api/openclaw/ws",
        "token": "test-token", "target": "ARCA_sandbox@0:20003",
        "expires_at": "2099-01-01T00:00:00Z", "a2a_base_url": BASE,
        "a2a_auth": "proxypass", "baas_base_url": "https://baas.example",
        "bot_uuid": "BOT-test", "tenant": "test", "engine_port": 20003,
    } | info_changes))
    svc._a2a_issuance_gate = MagicMock()
    svc._a2a_issuance_gate.allows_face.return_value = allowed
    return svc


def _connect(svc):
    return svc.get_device_connection(
        binding_id=1, operator=OperatorContext(staff_id="owner", staff="", nick_name="", operator_name=""),
        caller_id="actual-caller", ws_conn_mode="relay", path="/api/openclaw/ws",
        device_uuid="selected-instance",
    )


def test_allowed_caller_gets_proxy_face_same_target_token_and_expiry():
    svc = _service()
    result = _connect(svc)
    assert result.a2a_base_url == BASE
    assert result.a2a_proxy_token == result.token == "test-token"
    assert result.expires_at == "2099-01-01T00:00:00Z"
    assert not result.a2a_withheld
    svc._a2a_issuance_gate.allows_face.assert_called_once_with("actual-caller")
    svc._baas_service.get_ws_info.assert_called_once_with(
        bind_id=1, device_affinity="owner", device_uuid="selected-instance",
        ws_conn_mode="relay", path="/api/openclaw/ws",
    )


@pytest.mark.parametrize("changes", [
    {"allowed": False}, {"engine": "claude_code"}, {"engine": "hermes"},
    {"engine": "deepseek_harness"}, {"engine": "aicoding"}, {"engine": "teclaw"},
    {"bot_type": "desktop"}, {"a2a_auth": "unknown"},
    {"a2a_base_url": BASE + "/bad"}, {"expires_at": "expired"},
])
def test_rejected_face_never_removes_original_ws(changes):
    svc = _service(**changes)
    result = _connect(svc)
    assert result.a2a_base_url == result.a2a_proxy_token == ""
    assert result.a2a_withheld
    assert result.url == svc._baas_service.get_ws_info.return_value.ws_url
    assert result.token == "test-token"


def test_gate_failure_withholds_credentials_not_ws():
    svc = _service()
    svc._a2a_issuance_gate.allows_face.side_effect = RuntimeError("unavailable")
    result = _connect(svc)
    assert result.a2a_withheld and not result.a2a_proxy_token
    assert result.url and result.token == "test-token"


def test_direct_a2a_and_old_baas_responses_keep_their_behavior():
    svc = _service(a2a_auth="", a2a_base_url="http://localhost:20003")
    assert _connect(svc).a2a_base_url == "http://localhost:20003"
    assert _connect(svc).a2a_proxy_token == ""
    del svc._baas_service.get_ws_info.return_value.a2a_auth
    svc._baas_service.get_ws_info.return_value.a2a_base_url = ""
    result = _connect(svc)
    assert not result.a2a_withheld and not result.a2a_base_url
