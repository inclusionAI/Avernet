from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.resources import dependencies
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineResourceNotFoundError,
    EngineStageReadOnlyError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_default_resource_request_retains_legacy_resolution(monkeypatch, method):
    resolve = Mock()
    monkeypatch.setattr(dependencies, "resolve_stage_device_context", resolve)
    assert (
        await dependencies.resource_target(
            "bot", "owner", Request({"type": "http", "method": method})
        )
        == {}
    )
    resolve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", list(RuntimeStage))
async def test_selected_resource_context_is_resolved_once(monkeypatch, stage):
    context = SimpleNamespace(provider="baas", bot_type="service")
    resolve = Mock(return_value=context)
    monkeypatch.setattr(dependencies, "resolve_stage_device_context", resolve)
    result = await dependencies.resource_target(
        "bot",
        "owner",
        Request({"type": "http", "method": "GET"}),
        stage=stage,
        device_uuid="device-a",
        resolver="resolver",
        publications="publications",
        bindings="bindings",
        bots="bots",
    )
    assert result == {"runtime_context": context}
    resolve.assert_called_once_with(
        "resolver",
        "publications",
        "bindings",
        "bots",
        bot_id="bot",
        owner_id="owner",
        stage=stage.value,
        device_uuid="device-a",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [RuntimeStage.VERIFY, RuntimeStage.ONLINE])
async def test_selected_published_resource_is_not_writable(monkeypatch, stage):
    resolve = Mock()
    monkeypatch.setattr(dependencies, "resolve_stage_device_context", resolve)
    with pytest.raises(EngineStageReadOnlyError):
        await dependencies.resource_target(
            "bot",
            "owner",
            Request({"type": "http", "method": "POST"}),
            stage=stage,
            device_uuid="device-a",
        )
    resolve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,bot_type", [("local", "personal"), ("baas", "desktop")])
async def test_unsupported_instance_selection_is_rejected(monkeypatch, provider, bot_type):
    monkeypatch.setattr(
        dependencies,
        "resolve_stage_device_context",
        Mock(return_value=SimpleNamespace(provider=provider, bot_type=bot_type)),
    )
    with pytest.raises(EngineResourceNotFoundError):
        await dependencies.resource_target(
            "bot",
            "owner",
            Request({"type": "http", "method": "GET"}),
            device_uuid="device-a",
        )
