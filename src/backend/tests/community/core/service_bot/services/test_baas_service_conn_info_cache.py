"""TTL cache for BaasService connection-info lookups (ws-info / http-info).

Evidence (2026-08-28 pre, trace 0b446a1717878836076095311e05a8): every cron
fan-out target resolves BaaS http/ws info over HTTP (130-160 ms each) with no
reuse across requests. BaasService is a DI singleton, so a 30 s process-level
cache — the TTL already validated by ``baas_invoke_transport._HTTP_INFO_TTL_SECONDS``
— serialises nothing and removes the per-request round trips.

Covered here: hit on repeated params, miss on param variation, TTL expiry,
errors not cached, ``force_refresh`` bypass, cap eviction, and the same set
for ``get_ws_info_by_bot_uuid``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services import baas_service as baas_module
from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


class _FakeClock:
    """Stand-in for the ``time`` module, advanceable in tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _make_service() -> tuple[BaasService, LocalHttpClient]:
    http = LocalHttpClient(base_url="http://baas.test")
    service = BaasService(
        deploy_composer=ManagedDeployConfigComposer(
            storage_path=MagicMock(),
            sandbox_registry=MagicMock(),
            bot_repo=MagicMock(),
        ),
        startup_script_reader=MagicMock(**{"get_body.return_value": ""}),
        baas_api_base="http://baas.test",
        tenant="tnt",
        template_uuid="tpl",
        bot_repo=MagicMock(),
        bot_publish_repo=MagicMock(),
        system_config_service=MagicMock(),
        storage_path=MagicMock(),
        device_binding_repo=MagicMock(),
        default_ttl_minutes=10080,
        sandbox_registry=MagicMock(),
        http_client=http,
        general_http_client=LocalHttpClient(base_url=""),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
        outbound_rule_provider=MagicMock(),
    )
    service._device_binding_repo.get_by_id.return_value = SimpleNamespace(
        device_id="BOT-1"
    )
    return service, http


def _stub_http_info_response(
    http: LocalHttpClient, *, token: str = "tok"
) -> None:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "code": 0,
        "data": {"http_url": "http://container:20010", "token": token, "target": "TGT"},
    }
    http.set_response("get", mock)


@pytest.mark.unit
def test_get_http_info_reuses_cached_result_between_calls():
    service, http = _make_service()
    _stub_http_info_response(http)

    first = service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    second = service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    assert first is second
    assert len(http.calls_to("get")) == 1


@pytest.mark.unit
def test_get_http_info_cache_key_distinguishes_params():
    service, http = _make_service()
    _stub_http_info_response(http)

    service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    service.get_http_info(bind_id=7, port=20010, path="/api/cron", device_uuid="DEV-2")
    service.get_http_info(bind_id=8, port=20010, path="/api/cron")

    assert len(http.calls_to("get")) == 3


@pytest.mark.unit
def test_get_http_info_cached_entry_expires_after_ttl(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(baas_module, "time", clock)
    service, http = _make_service()
    _stub_http_info_response(http, token="tok-1")

    service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    clock.now += baas_module.BAAS_CONN_INFO_TTL_SECONDS + 1
    _stub_http_info_response(http, token="tok-2")

    result = service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    assert result.token == "tok-2"
    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_get_http_info_does_not_cache_failures():
    service, http = _make_service()
    boom = MagicMock()
    boom.raise_for_status.return_value = None
    boom.json.return_value = {"code": 1, "message": "BaaS exploded"}
    http.set_response("get", boom)

    with pytest.raises(BaasServiceError):
        service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    _stub_http_info_response(http)
    result = service.get_http_info(bind_id=7, port=20010, path="/api/cron")

    assert result.token == "tok"
    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_get_http_info_force_refresh_bypasses_cache():
    service, http = _make_service()
    _stub_http_info_response(http)

    service.get_http_info(bind_id=7, port=20010, path="/api/cron")
    result = service.get_http_info(
        bind_id=7, port=20010, path="/api/cron", force_refresh=True
    )

    assert result.token == "tok"
    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_conn_info_cache_evicts_when_over_cap(monkeypatch):
    monkeypatch.setattr(baas_module, "BAAS_CONN_INFO_CACHE_MAX_ENTRIES", 2)
    service, http = _make_service()
    _stub_http_info_response(http)
    service._device_binding_repo.get_by_id.side_effect = lambda bid: SimpleNamespace(
        device_id=f"BOT-{bid}"
    )

    service.get_http_info(bind_id=1, port=20010, path="/api/cron")
    service.get_http_info(bind_id=2, port=20010, path="/api/cron")
    service.get_http_info(bind_id=3, port=20010, path="/api/cron")  # cap hit -> clear
    service.get_http_info(bind_id=1, port=20010, path="/api/cron")  # re-resolve

    assert len(http.calls_to("get")) == 4


# ── get_ws_info_by_bot_uuid ──────────────────────────────────────────────


def _stub_ws_info_response(http: LocalHttpClient, *, token: str = "wtok") -> None:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "code": 0,
        "data": {
            "ws_url": "ws://container:20003/api/openclaw/ws",
            "token": token,
            "target": "TGT",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }
    http.set_response("get", mock)


@pytest.mark.unit
def test_get_ws_info_by_bot_uuid_reuses_cached_result_between_calls():
    service, http = _make_service()
    _stub_ws_info_response(http)

    first = service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    second = service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    assert first is second
    assert len(http.calls_to("get")) == 1


@pytest.mark.unit
def test_get_ws_info_cache_key_distinguishes_params():
    service, http = _make_service()
    _stub_ws_info_response(http)

    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-2")
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1", device_affinity="240841")

    assert len(http.calls_to("get")) == 3


@pytest.mark.unit
def test_get_ws_info_cached_entry_expires_after_ttl(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(baas_module, "time", clock)
    service, http = _make_service()
    _stub_ws_info_response(http, token="wtok-1")

    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    clock.now += baas_module.BAAS_CONN_INFO_TTL_SECONDS + 1
    _stub_ws_info_response(http, token="wtok-2")

    result = service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    assert result.token == "wtok-2"
    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_get_ws_info_by_bot_uuid_force_refresh_bypasses_cache():
    service, http = _make_service()
    _stub_ws_info_response(http)

    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1", force_refresh=True)

    assert len(http.calls_to("get")) == 2


@pytest.mark.unit
def test_get_ws_info_error_is_not_cached():
    service, http = _make_service()
    boom = MagicMock()
    boom.raise_for_status.return_value = None
    boom.json.return_value = {"code": 1, "message": "ws-info exploded"}
    http.set_response("get", boom)

    with pytest.raises(BaasServiceError):
        service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    _stub_ws_info_response(http)
    service.get_ws_info_by_bot_uuid(bot_uuid="BOT-1")

    assert len(http.calls_to("get")) == 2
