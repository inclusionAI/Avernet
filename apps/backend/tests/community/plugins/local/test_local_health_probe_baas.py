"""Unit tests for LocalHealthProbe BaaS mode (plan-04)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    HttpConnectionInfo,
)
from agentclaw.community.plugins.local.health_probe import LocalHealthProbe


def _make_binding(**overrides) -> DeviceBindingRecord:
    defaults = dict(
        id=42,
        entity_id="staff_u001",
        entity_type="staff",
        device_id="bot-uuid-001",
        device_provider="local",
        env="dev",
        device_props={
            "adapter_port": 20010,
            "tenant": "team_claw",
            "bolt_id": "bot-001",
        },
        status="ACTIVE",
        apply_reason=None,
        applied_by="staff_u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=None,
        gmt_modified=None,
    )
    defaults.update(overrides)
    return DeviceBindingRecord(**defaults)


def _make_probe(
    binding_repo=None, baas_service=None, bot_repo=None
) -> LocalHealthProbe:
    return LocalHealthProbe(
        binding_repo=binding_repo or MagicMock(),
        baas_service=baas_service or MagicMock(),
        bot_repository=bot_repo or MagicMock(),
    )


def test_ctor_accepts_new_deps():
    probe = _make_probe()
    assert probe is not None
    assert probe.mode_label == "local"


class _AsyncClientFactory:
    """Patch httpx.AsyncClient → returns canned responses by request URL."""

    def __init__(self, url_to_response: dict[str, httpx.Response]):
        self._map = url_to_response

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kwargs):
        if url in self._map:
            return self._map[url]
        return httpx.Response(
            500, json={"error": "not_mocked"},
            request=httpx.Request("GET", url),
        )


@pytest.mark.asyncio
async def test_engine_health_single_binding_happy():
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (1, [_make_binding()])

    baas_service = MagicMock()
    baas_service.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010/readiness", token="abc"
    )

    probe = _make_probe(binding_repo=binding_repo, baas_service=baas_service)

    response_map = {
        "http://10.0.0.1:20010/readiness": httpx.Response(
            200, json={"state": "ready"},
            request=httpx.Request("GET", "http://10.0.0.1:20010/readiness"),
        ),
    }

    with patch("httpx.AsyncClient", _AsyncClientFactory(response_map)):
        result = await probe.engine_health("staff_u001")

    assert len(result) == 1
    assert result[0]["state"] == "ready"
    assert result[0]["bot_id"] == "bot-001"
    assert result[0]["device_id"] == "bot-uuid-001"


@pytest.mark.asyncio
async def test_engine_health_empty_bindings_returns_empty_list():
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (0, [])
    probe = _make_probe(binding_repo=binding_repo)
    assert await probe.engine_health("staff_u001") == []


@pytest.mark.asyncio
async def test_engine_health_baas_failure_marks_unhealthy_no_raise():
    """单 binding BaaS 探活失败 → 该 entry 标 state=unhealthy + reason，不炸 list。"""
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (1, [_make_binding()])

    baas_service = MagicMock()
    baas_service.get_http_info.side_effect = BaasServiceError("baas down")

    probe = _make_probe(binding_repo=binding_repo, baas_service=baas_service)
    result = await probe.engine_health("staff_u001")
    assert len(result) == 1
    assert result[0]["state"] == "unhealthy"
    assert "baas down" in result[0].get("reason", "").lower()


@pytest.mark.asyncio
async def test_engine_health_multi_binding_partial_failure_isolates():
    """多个 binding 部分失败 → 不影响其他 binding 的结果。"""
    binding1 = _make_binding(id=1, device_id="ok-1")
    binding2 = _make_binding(id=2, device_id="bad-2")
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (2, [binding1, binding2])

    baas_service = MagicMock()

    def _get_http_info_side(bind_id, **_):
        if bind_id == 1:
            return HttpConnectionInfo(http_url="http://1.1.1.1:20010/readiness", token="t")
        raise BaasServiceError("baas down for 2")

    baas_service.get_http_info.side_effect = _get_http_info_side

    probe = _make_probe(binding_repo=binding_repo, baas_service=baas_service)
    response_map = {
        "http://1.1.1.1:20010/readiness": httpx.Response(
            200, json={"state": "ready"},
            request=httpx.Request("GET", "http://1.1.1.1:20010/readiness"),
        ),
    }
    with patch("httpx.AsyncClient", _AsyncClientFactory(response_map)):
        result = await probe.engine_health("staff_u001")

    assert len(result) == 2
    by_device = {r["device_id"]: r for r in result}
    assert by_device["ok-1"]["state"] == "ready"
    assert by_device["bad-2"]["state"] == "unhealthy"


@pytest.mark.asyncio
async def test_readiness_single_binding_happy():
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (1, [_make_binding()])

    baas_service = MagicMock()
    baas_service.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010/readiness", token="abc"
    )

    probe = _make_probe(binding_repo=binding_repo, baas_service=baas_service)
    response_map = {
        "http://10.0.0.1:20010/readiness": httpx.Response(
            200, json={"state": "ready", "version": "1.2.3"},
            request=httpx.Request("GET", "http://10.0.0.1:20010/readiness"),
        ),
    }

    with patch("httpx.AsyncClient", _AsyncClientFactory(response_map)):
        result = await probe.readiness("staff_u001", grace_seconds=30)

    assert len(result) == 1
    assert result[0]["state"] == "ready"
    assert result[0]["version"] == "1.2.3"


@pytest.mark.asyncio
async def test_readiness_empty_bindings_returns_empty_list():
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (0, [])
    probe = _make_probe(binding_repo=binding_repo)
    assert await probe.readiness("staff_u001", grace_seconds=30) == []


@pytest.mark.asyncio
async def test_bots_health_happy():
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (1, [_make_binding()])
    baas_service = MagicMock()
    baas_service.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010/readiness", token="abc"
    )
    probe = _make_probe(binding_repo=binding_repo, baas_service=baas_service)
    response_map = {
        "http://10.0.0.1:20010/readiness": httpx.Response(
            200, json={"state": "ready"},
            request=httpx.Request("GET", "http://10.0.0.1:20010/readiness"),
        ),
    }
    with patch("httpx.AsyncClient", _AsyncClientFactory(response_map)):
        result = await probe.bots_health("staff_u001")
    assert len(result) == 1
    assert result[0]["healthy"] is True
    assert result[0]["bot_id"] == "bot-001"
    assert result[0]["device_id"] == "bot-uuid-001"


@pytest.mark.asyncio
async def test_sandbox_health_returns_not_supported_per_spec():
    probe = _make_probe()
    result = await probe.sandbox_health("bot-001", "staff_u001")
    assert result["code"] == 1
    assert "not supported" in result["message"].lower()
    assert result["instances"] == []
