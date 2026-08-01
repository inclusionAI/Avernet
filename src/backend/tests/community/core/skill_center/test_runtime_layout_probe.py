from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    CurrentRuntimeLayoutProbeService,
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
)


def _service(
    *,
    response: dict | None = None,
    transport_error: Exception | None = None,
    resolver_error: Exception | None = None,
):
    context = Mock(conn_info={"url": "http://current-runtime"})
    resolver = Mock()
    if resolver_error is not None:
        resolver.resolve_for_bot.side_effect = resolver_error
    else:
        resolver.resolve_for_bot.return_value = context
    transport = Mock()
    if transport_error is not None:
        transport.invoke = AsyncMock(side_effect=transport_error)
    else:
        transport.invoke = AsyncMock(
            return_value=response
            or {
                "success": True,
                "data": {
                    "status": "READY",
                    "engine": "openclaw",
                    "layout_contract_version": "skills-pool-p3-v1",
                    "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
                    "evidence": {
                        "mapping_contract_version": "skills-pool-mapping-v2",
                        "checks": {
                            "pool_repo_mounted": True,
                            "legacy_repo_bridge_valid": True,
                        }
                    },
                },
            }
        )
    service = CurrentRuntimeLayoutProbeService(
        resolver=resolver,
        adapter_transport=transport,
    )
    return service, resolver, transport, context


@pytest.mark.asyncio
async def test_ready_is_taken_from_current_runtime_inspection():
    service, resolver, transport, context = _service()

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.READY
    assert result.preparation_id == "2a958f59-8cf4-4413-a267-7d56d3382f23"
    resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
    transport.invoke.assert_awaited_once_with(
        context.conn_info,
        "POST",
        "/api/skills/layout/probe",
        body={
            "engine": "openclaw",
            "layout_contract_version": "skills-pool-p3-v1",
        },
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_claude_code_ready_uses_current_runtime_probe():
    service, resolver, transport, context = _service(
        response={
            "success": True,
            "data": {
                "status": "READY",
                "engine": "claude_code",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
                "evidence": {
                    "mapping_contract_version": "skills-pool-mapping-v2",
                    "checks": {
                        "stable_local_bridge_valid": True,
                        "stable_repo_bridge_valid": True,
                    }
                },
            },
        }
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="claude_code",
    )

    assert result.status is RuntimeLayoutProbeStatus.READY
    assert result.engine == "claude_code"
    resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
    transport.invoke.assert_awaited_once_with(
        context.conn_info,
        "POST",
        "/api/skills/layout/probe",
        body={
            "engine": "claude_code",
            "layout_contract_version": "skills-pool-p3-v1",
        },
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_aicoding_ready_uses_current_runtime_probe():
    service, resolver, transport, context = _service(
        response={
            "success": True,
            "data": {
                "status": "READY",
                "engine": "aicoding",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
                "evidence": {
                    "mapping_contract_version": "skills-pool-mapping-v2",
                    "checks": {
                        "stable_local_bridge_valid": True,
                        "stable_repo_bridge_valid": True,
                    }
                },
            },
        }
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="aicoding",
    )

    assert result.status is RuntimeLayoutProbeStatus.READY
    assert result.engine == "aicoding"
    resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
    transport.invoke.assert_awaited_once_with(
        context.conn_info,
        "POST",
        "/api/skills/layout/probe",
        body={
            "engine": "aicoding",
            "layout_contract_version": "skills-pool-p3-v1",
        },
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_hermes_ready_requires_current_runtime_h0_evidence():
    service, resolver, transport, context = _service(
        response={
            "success": True,
            "data": {
                "status": "READY",
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
                "evidence": {
                    "mapping_contract_version": "skills-pool-mapping-v2",
                    "checks": {
                        "legacy_local_bridge_valid": True,
                        "stable_repo_bridge_valid": True,
                    }
                },
            },
        }
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="hermes",
    )

    assert result.status is RuntimeLayoutProbeStatus.READY
    assert result.engine == "hermes"
    assert result.evidence["checks"]["legacy_local_bridge_valid"] is True
    resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
    transport.invoke.assert_awaited_once_with(
        context.conn_info,
        "POST",
        "/api/skills/layout/probe",
        body={
            "engine": "hermes",
            "layout_contract_version": "skills-pool-p3-v1",
        },
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_new_runtime_without_marker_is_not_capable():
    service, *_ = _service(
        response={
            "success": True,
            "data": {
                "status": "NOT_CAPABLE",
                "engine": "openclaw",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": None,
                "evidence": {"reason": "pool_ready_marker_absent"},
            },
        }
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.NOT_CAPABLE
    assert result.evidence["reason"] == "pool_ready_marker_absent"


@pytest.mark.asyncio
async def test_ready_runtime_without_mapping_v2_is_not_capable():
    service, *_ = _service(
        response={
            "success": True,
            "data": {
                "status": "READY",
                "engine": "openclaw",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
                "evidence": {"checks": {"pool_repo_mounted": True}},
            },
        }
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.NOT_CAPABLE
    assert result.evidence == {
        "reason": "logical_mapping_contract_not_supported"
    }


@pytest.mark.asyncio
async def test_runtime_invalid_result_stays_invalid():
    response = {
        "success": True,
        "data": {
            "status": "INVALID",
            "engine": "openclaw",
            "layout_contract_version": "skills-pool-p3-v1",
            "preparation_id": "prep-1",
            "evidence": {"reason": "legacy_repo_bridge_invalid"},
        },
    }
    service, *_ = _service(response=response)

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.INVALID
    assert result.evidence["reason"] == "legacy_repo_bridge_invalid"


@pytest.mark.asyncio
async def test_unreachable_runtime_is_transient():
    service, *_ = _service(transport_error=TimeoutError("unreachable"))

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "runtime_probe_failed"


@pytest.mark.asyncio
async def test_bot_without_current_binding_is_not_capable():
    service, *_ = _service(resolver_error=DeviceNotBoundError("no active binding"))

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.NOT_CAPABLE
    assert result.evidence["reason"] == "current_runtime_not_bound"


@pytest.mark.asyncio
async def test_unknown_provider_is_invalid_instead_of_retried():
    service, *_ = _service(resolver_error=UnknownProviderError("broken provider"))

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.INVALID
    assert result.evidence["reason"] == "current_runtime_provider_invalid"


@pytest.mark.asyncio
async def test_marker_from_newer_nas_but_old_endpoint_is_not_capable():
    service, _, transport, context = _service()
    transport.invoke = AsyncMock(
        side_effect=[
            DeviceAdapterEndpointNotFoundError("old image"),
            {"status": "ok"},
        ]
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.NOT_CAPABLE
    assert result.evidence["reason"] == "runtime_layout_probe_endpoint_absent"
    assert transport.invoke.await_args_list[1].args == (
        context.conn_info,
        "GET",
        "/health",
    )
    assert transport.invoke.await_args_list[1].kwargs == {"timeout": 5.0}


@pytest.mark.asyncio
async def test_proxy_target_404_is_transient_when_liveness_also_fails():
    service, _, transport, _ = _service()
    transport.invoke = AsyncMock(
        side_effect=DeviceAdapterEndpointNotFoundError("proxy target missing")
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "runtime_probe_failed"
    assert transport.invoke.await_count == 2


@pytest.mark.asyncio
async def test_probe_404_then_health_401_is_invalid():
    service, _, transport, _ = _service()
    transport.invoke = AsyncMock(
        side_effect=[
            DeviceAdapterEndpointNotFoundError("probe endpoint absent"),
            DeviceAdapterHTTPStatusError(401, "unauthorized"),
        ]
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.INVALID
    assert result.evidence["reason"] == "runtime_probe_rejected"


@pytest.mark.asyncio
async def test_permanent_http_rejection_is_invalid():
    service, *_ = _service(
        transport_error=DeviceAdapterHTTPStatusError(401, "unauthorized")
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.INVALID
    assert result.evidence["reason"] == "runtime_probe_rejected"


@pytest.mark.asyncio
async def test_runtime_http_500_is_transient():
    service, *_ = _service(
        transport_error=DeviceAdapterHTTPStatusError(500, "unavailable")
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR


@pytest.mark.asyncio
async def test_malformed_runtime_response_is_invalid():
    service, *_ = _service(response={"success": True, "data": {"status": "READY"}})

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.INVALID
    assert result.evidence["reason"] == "invalid_runtime_probe_response"


@pytest.mark.asyncio
async def test_teclaw_is_noop_without_resolving_runtime():
    service, resolver, transport, _ = _service(
        transport_error=AssertionError("must not touch runtime")
    )

    result = await service.probe_bot(
        bot_id="bot-1",
        user_id="user-1",
        engine="teclaw",
    )

    assert result == RuntimeLayoutProbeResult(
        status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
        engine="teclaw",
        layout_contract_version="skills-pool-p3-v1",
        preparation_id=None,
        evidence={"reason": "engine_has_no_filesystem_pool_layout"},
    )
    resolver.resolve_for_bot.assert_not_called()
    transport.invoke.assert_not_awaited()


def test_real_engine_response_schema_is_accepted(monkeypatch):
    engine_source = Path(__file__).resolve().parents[5] / "engine" / "src"
    monkeypatch.syspath_prepend(str(engine_source))
    from engine.community.api.skills.schemas import (
        RuntimeLayoutProbeApiResponse,
        RuntimeLayoutProbeResponse,
    )

    engine_response = RuntimeLayoutProbeApiResponse(
        success=True,
        data=RuntimeLayoutProbeResponse(
            status="READY",
            engine="openclaw",
            layout_contract_version="skills-pool-p3-v1",
            preparation_id="2a958f59-8cf4-4413-a267-7d56d3382f23",
            evidence={
                "mapping_contract_version": "skills-pool-mapping-v2",
                "checks": {"pool_repo_mounted": True},
            },
        ),
        message="运行时 Skills Pool 布局探测完成",
    )

    result = CurrentRuntimeLayoutProbeService._parse_response(
        engine_response.model_dump(mode="json"),
        engine="openclaw",
    )

    assert result.status is RuntimeLayoutProbeStatus.READY
    assert result.preparation_id == "2a958f59-8cf4-4413-a267-7d56d3382f23"
