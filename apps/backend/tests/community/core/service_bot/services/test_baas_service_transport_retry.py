"""Transient-transport handling for ``BaasService`` read calls.

Covers the fix for the agentclaw production report
``[PublishFlowService.get_baas_publish_progress] Failed: Error in httpx send
hook``: ``ant_baselib``'s httpx tracing hook re-raises a generic message and
keeps the real timeout/disconnect only in the exception chain.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services import baas_service as baas_module
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
    _error_chain,
    _retry_transient,
)
from agentclaw.community.core.service_bot.services.publish_flow.baas_publish_ops_mixin import (
    BaasPublishOpsMixin,
)


class _FlakyHttp:
    """HttpClient stub: each ``get`` pops the next outcome (response or error)."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _progress_response() -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"code": 0, "data": {"status": "PENDING"}}
    return response


def _service_with_http(http: _FlakyHttp) -> BaasService:
    # Only the two attributes get_publish_progress reads; avoids the full DI
    # wiring that is unrelated to transport behaviour.
    service = BaasService.__new__(BaasService)
    service._http = http
    service._tenant = "team_claw"
    return service


def test_error_chain_surfaces_hook_wrapped_cause():
    """The hook's generic message alone hides the real transport failure."""
    underlying = httpx.ReadTimeout("The read operation timed out")
    try:
        raise underlying
    except httpx.ReadTimeout as cause:
        wrapped = RuntimeError("Error in httpx send hook")
        wrapped.__cause__ = cause
        rendered = _error_chain(wrapped)

    assert "Error in httpx send hook" in rendered
    assert "The read operation timed out" in rendered


def test_error_chain_is_cycle_safe():
    """A self-referential ``__context__`` must not loop forever."""
    exc = RuntimeError("boom")
    exc.__context__ = exc

    assert _error_chain(exc) == "boom"


def test_retry_transient_does_not_retry_application_error():
    """A non-transport error must surface on the first attempt."""
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise ValueError("bad payload")

    with pytest.raises(ValueError, match="bad payload"):
        _retry_transient("op", boom)

    assert len(calls) == 1


def test_get_publish_progress_retries_transient_then_succeeds(monkeypatch):
    """Two transport failures are absorbed; the third attempt returns data."""
    sleeps: list[float] = []
    monkeypatch.setattr(baas_module.time, "sleep", sleeps.append)
    http = _FlakyHttp(
        [
            httpx.ReadTimeout("The read operation timed out"),
            httpx.RemoteProtocolError("Server disconnected"),
            _progress_response(),
        ]
    )

    result = _service_with_http(http).get_publish_progress(publish_id=97872)

    assert result["status"] == "PENDING"
    assert len(http.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_get_publish_progress_retries_hook_wrapped_timeout(monkeypatch):
    """The ant_baselib hook wrapper is classified by its underlying cause."""
    sleeps: list[float] = []
    monkeypatch.setattr(baas_module.time, "sleep", sleeps.append)
    wrapped = RuntimeError("Error in httpx send hook")
    wrapped.__cause__ = httpx.ReadTimeout("The read operation timed out")
    http = _FlakyHttp([wrapped, _progress_response()])

    result = _service_with_http(http).get_publish_progress(publish_id=97872)

    assert result["status"] == "PENDING"
    assert len(http.calls) == 2
    assert sleeps == [1.0]


def test_get_publish_progress_does_not_retry_non_transport_error(monkeypatch):
    """Application errors keep their existing single-attempt wrapping."""
    sleeps: list[float] = []
    monkeypatch.setattr(baas_module.time, "sleep", sleeps.append)
    http = _FlakyHttp([ValueError("bad payload")])

    with pytest.raises(BaasServiceError, match="Failed to get publish progress"):
        _service_with_http(http).get_publish_progress(publish_id=97872)

    assert len(http.calls) == 1
    assert sleeps == []


def test_get_publish_progress_exhausts_retries_with_diagnosable_error(monkeypatch):
    """After the last retry the error names the attempt count and real cause."""
    sleeps: list[float] = []
    monkeypatch.setattr(baas_module.time, "sleep", sleeps.append)
    http = _FlakyHttp(
        [httpx.ReadTimeout("The read operation timed out") for _ in range(4)]
    )

    with pytest.raises(BaasServiceError) as exc_info:
        _service_with_http(http).get_publish_progress(publish_id=97872)

    message = str(exc_info.value)
    assert "get_publish_progress(publish_id=97872) failed after 4 attempts" in message
    assert "The read operation timed out" in message
    assert len(http.calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_list_devices_by_bot_uuid_retries_transient(monkeypatch):
    """The other read-only BaaS GETs share the same retry policy (AC-4)."""
    sleeps: list[float] = []
    monkeypatch.setattr(baas_module.time, "sleep", sleeps.append)
    success = MagicMock()
    success.raise_for_status.return_value = None
    success.json.return_value = {"code": 0, "data": [{"items": [{"device_id": "d1"}]}]}
    http = _FlakyHttp([httpx.ConnectError("connection refused"), success])

    items = _service_with_http(http).list_devices_by_bot_uuid("bot-uuid-001")

    assert items == [{"device_id": "d1"}]
    assert len(http.calls) == 2
    assert sleeps == [1.0]


def test_get_http_info_retries_transient(monkeypatch):
    """http-info lookups share the retry policy and keep the AC-1 error chain."""
    sleeps: list[float] = []
    monkeypatch.setattr(baas_module.time, "sleep", sleeps.append)
    success = MagicMock()
    success.raise_for_status.return_value = None
    success.json.return_value = {
        "code": 0,
        "data": {
            "http_url": "http://10.0.0.1:20010",
            "token": "abc",
            "target": "TECLAW_b@4:20010",
        },
    }
    http = _FlakyHttp([httpx.ReadTimeout("The read operation timed out"), success])
    service = _service_with_http(http)
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = MagicMock(device_id="bot-uuid-001")
    service._device_binding_repo = binding_repo

    info = service.get_http_info(bind_id=42, port=20010)

    assert info.http_url == "http://10.0.0.1:20010"
    assert len(http.calls) == 2
    assert sleeps == [1.0]


def test_publish_ops_mixin_does_not_swallow_diagnosable_error():
    """The mixin logs and re-raises, so advance_publish_progress (which catches
    any Exception and returns a PublishFlowResult) receives the full message;
    the durable poll task's own retry behaviour is covered by its suite."""

    class _Ops(BaasPublishOpsMixin):
        pass

    ops = _Ops()
    ops._baas_service = MagicMock()
    ops._baas_service.get_publish_progress.side_effect = BaasServiceError(
        "get_publish_progress(publish_id=97872) failed after 4 attempts: "
        "Error in httpx send hook | The read operation timed out"
    )

    with pytest.raises(BaasServiceError) as exc_info:
        ops.get_baas_publish_progress(baas_publish_id=97872)

    assert "failed after 4 attempts" in str(exc_info.value)
    assert "The read operation timed out" in str(exc_info.value)