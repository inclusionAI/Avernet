"""Tests for retry behavior in BaasService.get_http_info.

The incident this covers: a single transport blip on ``GET /http-info`` failed a
whole multi-file skill upload. These tests assert **call counts**, not just
outcomes — an outcome-only assertion would pass even if retry never happened.

The complementary assertion lives here too: a response that *arrived* must not
be retried, whatever its status. That is what keeps existing status handling
unchanged.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
    HttpConnectionInfo,
)
from agentclaw.community.utils import retry as retry_mod


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never actually wait out the backoff."""
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _seconds: None)


@pytest.fixture
def fake_binding_repo():
    repo = MagicMock()
    binding = MagicMock()
    binding.device_id = "bot-uuid-001"
    repo.get_by_id.return_value = binding
    return repo


@pytest.fixture
def http():
    """HttpClient stub whose ``get`` replays a scripted sequence."""

    class _Http:
        def __init__(self) -> None:
            self.outcomes: list[object] = []
            self.calls = 0

        def script(self, *outcomes: object) -> None:
            self.outcomes = list(outcomes)

        def get(self, *args: object, **kwargs: object) -> object:
            self.calls += 1
            outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    return _Http()


@pytest.fixture
def baas_service(fake_binding_repo, http):
    return BaasService(
        baas_api_base="http://baas.fake",
        tenant="team_claw",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=fake_binding_repo,
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http,
        general_http_client=MagicMock(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )


def _ok_response() -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {
        "code": 0,
        "data": {
            "http_url": "http://10.0.0.1:20010",
            "token": "abc",
            "target": "TECLAW_b_teclaw1@4:20010",
        },
    }
    return r


def _wrapped_transport_error() -> Exception:
    """Mimic the prod send-hook wrapper: opaque type, real cause on __cause__.

    This is the shape that defeats ``except httpx.TimeoutException`` — the
    reason classification is by symptom rather than by exception type.
    """
    cause = httpx.ConnectTimeout("timed out")
    exc = RuntimeError("Error in httpx send hook")
    exc.__cause__ = cause
    return exc


def _call(baas_service) -> HttpConnectionInfo:
    return baas_service.get_http_info(bind_id=42, port=20010, path="/api/file/read")


def test_one_transport_failure_then_success(baas_service, http):
    """A single blip is absorbed: two calls, caller sees a normal result."""
    http.script(_wrapped_transport_error(), _ok_response())

    info = _call(baas_service)

    assert isinstance(info, HttpConnectionInfo)
    assert info.http_url == "http://10.0.0.1:20010"
    assert http.calls == 2


def test_persistent_transport_failure_raises_with_cause_chain(baas_service, http):
    """Exhaustion surfaces the underlying error, not the wrapper's message."""
    http.script(_wrapped_transport_error())

    with pytest.raises(BaasServiceError) as excinfo:
        _call(baas_service)

    assert http.calls == 2
    message = str(excinfo.value)
    # The wrapper alone would say only "Error in httpx send hook".
    assert "caused by ConnectTimeout" in message
    assert excinfo.value.__cause__ is not None


def test_error_status_response_is_not_retried(baas_service, http):
    """A 5xx that *arrived* is an answer — one call, existing behavior."""
    response = MagicMock()
    response.status_code = 503
    response.text = "upstream down"
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="503",
        request=httpx.Request("GET", "http://baas.fake/x"),
        response=httpx.Response(
            status_code=503,
            json={"code": 1},
            request=httpx.Request("GET", "http://baas.fake/x"),
        ),
    )
    http.script(response)

    with pytest.raises(BaasServiceError, match="BaaS API error: 503"):
        _call(baas_service)

    assert http.calls == 1


def test_client_error_exception_is_not_retried(baas_service, http):
    """An exception carrying a 4xx cannot succeed on repeat — one call."""

    class _Response:
        status_code = 404

    exc = RuntimeError("not found")
    exc.response = _Response()  # type: ignore[attr-defined]
    http.script(exc)

    with pytest.raises(BaasServiceError):
        _call(baas_service)

    assert http.calls == 1


def test_happy_path_makes_exactly_one_call(baas_service, http):
    """Adoption must not add a call on the path that already worked."""
    http.script(_ok_response())

    info = _call(baas_service)

    assert isinstance(info, HttpConnectionInfo)
    assert http.calls == 1


def test_business_error_code_is_not_retried(baas_service, http):
    """code != 0 is raised inside the try but outside the retried thunk."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"code": 1, "message": "bot not found"}
    http.script(response)

    with pytest.raises(BaasServiceError, match="bot not found"):
        _call(baas_service)

    assert http.calls == 1
