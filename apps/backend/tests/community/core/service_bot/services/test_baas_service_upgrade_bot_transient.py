"""Transient-transport handling for ``BaasService.upgrade_bot``.

Prod trace ``Error in httpx send hook: Server disconnected`` shows that when
``ant_baselib.tracer.patches.httpx_client`` re-raises an httpx transport
failure as a plain ``HttpxCallingException(...)`` (not an ``httpx.RequestError``
subclass), the upgrade-bot path's exception handlers miss the transient
classification and surface it as a permanent user-facing failure. These tests
pin the bounded single-retry + ``BaasTransientServiceError`` contract
introduced for that path, with both the direct ``httpx.RequestError`` case and
the ant_baselib-wrapped case, while keeping the ``httpx.HTTPStatusError`` (409 /
5xx) branch retry-free so the existing 409 in-flight contract is unchanged.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.service_bot.services import baas_service as baas_service_module
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.baas_service_errors import (
    BaasServiceError,
    BaasTransientServiceError,
)
from agentclaw.community.core.service_bot.services.deploy.managed_composer import (
    ManagedDeployConfigComposer,
)
from tests.community.core.service_bot.services.deploy._noop_storage_policy import (
    NoopStoragePolicy,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.local.outbound_rules import NoopOutboundRuleProvider


class _AntBaselibWrap(Exception):
    """Model ant_baselib's HttpxCallingException wrapper without importing it."""


def _make_service() -> tuple[BaasService, LocalHttpClient]:
    http = LocalHttpClient(base_url="http://baas.test")
    service = BaasService(
        deploy_composer=ManagedDeployConfigComposer(
            storage_path=MagicMock(),
            sandbox_registry=MagicMock(),
            bot_repo=MagicMock(),
            storage_policy=NoopStoragePolicy(),
        ),
        startup_script_reader=MagicMock(**{"get_body.return_value": ""}),
        baas_api_base="http://baas.test",
        tenant="team_claw",
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
        outbound_rule_provider=NoopOutboundRuleProvider(),
        secret_resolver=MagicMock(),
        common_whitelist_service=MagicMock(),
    )
    return service, http


def _resp_ok(publish_id: int) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"code": 0, "message": "ok", "data": {"bot_uuid": "BOT-1", "publish_id": publish_id}}
    return r


def _bot() -> dict:
    return {
        "id": 501,
        "bot_id": "bot-1",
        "bot_name": "Bot 1",
        "entity_id": "user-1",
        "entity_type": "staff",
        "active_engine": "claude_code",
        "bot_type": "service",
    }


def _upgrade_common_kwargs() -> dict:
    return dict(
        bot_uuid="BOT-1",
        bot=_bot(),
        owner_id="op",
        request_id="req-4",
        migration_path="/home/admin/nfs/bot-data/1/claude_code",
    )


def _httpx_request() -> httpx.Request:
    return httpx.Request("POST", "http://baas.test/api/v1/bots/BOT-1/update")


def _remote_protocol_error(msg: str = "Server disconnected", request=None) -> httpx.RemoteProtocolError:
    return httpx.RemoteProtocolError(msg, request=request or _httpx_request())


def _http_status_error(status_code: int, body: str = "conflict") -> httpx.HTTPStatusError:
    request = _httpx_request()
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


@pytest.mark.unit
class TestUpgradeBotTransientRetry:
    def test_ac1_transport_error_then_success_retries_once(self, monkeypatch):
        # AC1: first call raises httpx.RemoteProtocolError; second call succeeds
        # → upgrade_bot returns the success envelope and POST was issued twice.
        monkeypatch.setattr(
            baas_service_module, "_UPGRADE_TRANSIENT_RETRY_WAIT_SECONDS", 0.0
        )
        service, http = _make_service()

        def _impl(*a, **kw):
            _impl.calls = getattr(_impl, "calls", 0) + 1
            if _impl.calls == 1:
                raise _remote_protocol_error()
            return _resp_ok(publish_id=42)
        http.set_override("post", _impl)

        result = service.upgrade_bot(**_upgrade_common_kwargs())
        assert result["publish_id"] == 42
        assert len(http.calls_to("post")) == 2

    def test_ac2_transport_error_both_attempts_raises_transient(self, monkeypatch):
        # AC2: both attempts raise RemoteProtocolError → BaasTransientServiceError
        # (subclass of BaasServiceError), with a non-None __cause__.
        monkeypatch.setattr(
            baas_service_module, "_UPGRADE_TRANSIENT_RETRY_WAIT_SECONDS", 0.0
        )
        service, http = _make_service()

        def _impl(*a, **kw):
            raise _remote_protocol_error()
        http.set_override("post", _impl)

        with pytest.raises(BaasTransientServiceError, match="after retry") as excinfo:
            service.upgrade_bot(**_upgrade_common_kwargs())

        assert isinstance(excinfo.value, BaasServiceError)
        assert excinfo.value.__cause__ is not None
        assert isinstance(excinfo.value.__cause__, httpx.RequestError)

    def test_ac3_ant_baselib_wrap_then_success_retries_once(self, monkeypatch):
        # AC3: ant_baselib HttpxCallingException wrap with __cause__ = httpx
        # .RequestError is unwrapped → same retry-and-recover behavior as AC1.
        monkeypatch.setattr(
            baas_service_module, "_UPGRADE_TRANSIENT_RETRY_WAIT_SECONDS", 0.0
        )
        service, http = _make_service()

        def _impl(*a, **kw):
            _impl.calls = getattr(_impl, "calls", 0) + 1
            if _impl.calls == 1:
                try:
                    raise _remote_protocol_error()
                except httpx.RequestError as base:
                    raise _AntBaselibWrap("Error in httpx send hook") from base
            return _resp_ok(publish_id=99)
        http.set_override("post", _impl)

        result = service.upgrade_bot(**_upgrade_common_kwargs())
        assert result["publish_id"] == 99
        assert len(http.calls_to("post")) == 2

    def test_ac3_ant_baselib_wrap_both_attempts_raises_transient(self, monkeypatch):
        # AC3 (failure path): both attempts raise the ant_baselib wrapper →
        # digs the underlying RequestError and raises BaasTransientServiceError.
        monkeypatch.setattr(
            baas_service_module, "_UPGRADE_TRANSIENT_RETRY_WAIT_SECONDS", 0.0
        )
        service, http = _make_service()

        def _impl(*a, **kw):
            try:
                raise _remote_protocol_error()
            except httpx.RequestError as base:
                raise _AntBaselibWrap("Error in httpx send hook") from base
        http.set_override("post", _impl)

        with pytest.raises(BaasTransientServiceError, match="after retry"):
            service.upgrade_bot(**_upgrade_common_kwargs())

    def test_ac4_http_status_error_409_is_not_retried(self):
        # AC4: a 409 (and 500) HTTPStatusError is NOT retried (preserves the 409
        # in-flight contract — no double-issue risk).
        service, http = _make_service()
        http.set_override("post", lambda *a, **kw: (_ for _ in ()).throw(_http_status_error(409)))
        calls = http.calls_to

        with pytest.raises(httpx.HTTPStatusError):
            service.upgrade_bot(**_upgrade_common_kwargs())
        assert len(calls("post")) == 1

    def test_ac4_http_status_error_500_is_not_retried(self):
        # AC4: 5xx HTTPStatusError also keeps a single attempt.
        service, http = _make_service()
        http.set_override("post", lambda *a, **kw: (_ for _ in ()).throw(_http_status_error(500, "boom")))
        with pytest.raises(httpx.HTTPStatusError):
            service.upgrade_bot(**_upgrade_common_kwargs())
        assert len(http.calls_to("post")) == 1

    def test_ac5_happy_path_returns_publish_id(self):
        # AC5 (regression safeguard): no transient errors → unchanged success path.
        service, http = _make_service()
        http.set_response("post", _resp_ok(publish_id=7))
        result = service.upgrade_bot(**_upgrade_common_kwargs())
        assert result["publish_id"] == 7
        assert len(http.calls_to("post")) == 1
        assert http.calls_to("post")[0].args[0] == "/api/v1/bots/BOT-1/update"
