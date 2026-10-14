"""Release identity through the read-only Service API; only external seams fake."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.bot_management.engine_version.service import (
    EngineVersionService,
)
from agentclaw.community.core.bot_management.engine_version.models import (
    EngineVersionConfig,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    TemplateRepository,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)


@pytest.fixture
def dependencies():
    bots = MagicMock(spec=BotRepository)
    bots.get_by_id_and_owner.return_value = {
        "id": 1,
        "bot_id": "bot-1",
        "owner_id": "owner",
        "bot_type": "personal",
        "ext": {},
    }
    templates = MagicMock(spec=TemplateRepository)
    templates.get_by_bot_id.return_value = {"ext": {}}
    bindings = MagicMock(spec=DeviceBindingRepository)
    bindings.get_active_by_bot_and_owner.return_value = SimpleNamespace(
        id=7,
        device_provider="arca",
        device_id="instance-1",
        status="ACTIVE",
    )
    transport = AsyncMock(spec=DeviceAdapterTransport)
    transport.invoke.return_value = {
        "success": True,
        "data": {"stdout": "REL20261009\n", "stderr": "", "exit_code": 0},
    }
    resolver = MagicMock()
    resolver.resolve_for_binding_invoke.return_value = SimpleNamespace(
        conn_info={"target": "private-target"},
        provider="arca",
        binding_id=7,
    )
    return bots, templates, bindings, resolver, transport


@pytest.mark.asyncio
async def test_owner_can_compare_current_engine_to_paired_backend_release(dependencies):
    bots, templates, bindings, resolver, transport = dependencies
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["current"] == {"release_id": "REL20261009", "source": "engine_env"}
    assert result["release_target"]["release_id"] == "REL20261014"
    assert result["comparison"] == "older"
    assert result["restart_hint"] == "recommended"
    transport.invoke.assert_awaited_once_with(
        {"target": "private-target"},
        "POST",
        "/api/bash/exec",
        body={
            "cmd": "printenv AGENTCLAW_IMAGE_VERSION",
            "cwd": "/home/admin/",
            "timeout": 5,
        },
        timeout=10.0,
    )
    bots.get_by_id_and_owner.assert_called_once_with("bot-1", "owner")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [TimeoutError("secret"), ValueError("secret"), RuntimeError("secret")]
)
async def test_probe_failure_is_a_sanitized_detection_result(dependencies, failure):
    bots, templates, bindings, resolver, transport = dependencies
    transport.invoke.side_effect = failure
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["detection"]["status"] == "read_failed"
    assert result["restart_hint"] == "unknown"
    assert "secret" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["verify", "online", "arbitrary"])
async def test_published_stages_never_fall_back_to_draft(dependencies, stage):
    from agentclaw.community.core.bot_management.engine_version.errors import (
        EngineVersionStageUnsupportedError,
    )

    bots, templates, bindings, resolver, transport = dependencies
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    with pytest.raises(EngineVersionStageUnsupportedError):
        await service.inspect(
            bot_id="bot-1", owner_id="owner", caller_id="owner", stage=stage
        )
    transport.invoke.assert_not_called()
    bindings.get_active_by_bot_and_owner.assert_not_called()


@pytest.mark.asyncio
async def test_missing_binding_does_not_mean_legacy(dependencies):
    bots, templates, bindings, resolver, transport = dependencies
    bindings.get_active_by_bot_and_owner.return_value = None
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["detection"] == {
        "status": "read_failed",
        "reason": "device_not_bound",
    }
    transport.invoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ids", "selected", "accepted"),
    [
        (["one"], None, "one"),
        (["one", "two"], "two", "two"),
        (["one", "two"], None, None),
        (["one"], "absent", None),
    ],
)
async def test_multi_instance_probe_is_frozen_to_selected_device(
    dependencies, ids, selected, accepted
):
    from agentclaw.community.core.bot_management.engine_version.errors import (
        EngineVersionInstanceSelectionError,
    )

    bots, templates, bindings, resolver, transport = dependencies
    bindings.get_active_by_bot_and_owner.return_value.device_provider = "baas"
    inventory = MagicMock(return_value=[{"device_uuid": value} for value in ids])
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=inventory,
        config=EngineVersionConfig("REL20261014"),
    )
    if accepted is None:
        with pytest.raises(EngineVersionInstanceSelectionError):
            await service.inspect(
                bot_id="bot-1",
                owner_id="owner",
                caller_id="owner",
                device_uuid=selected,
            )
        transport.invoke.assert_not_called()
    else:
        result = await service.inspect(
            bot_id="bot-1", owner_id="owner", caller_id="owner", device_uuid=selected
        )
        assert result["context"]["instance_id"] == accepted
        resolver.resolve_for_binding_invoke.assert_called_once_with(
            7, "owner", bot_id="bot-1", device_uuid=accepted
        )


@pytest.mark.asyncio
async def test_failed_template_read_preserves_completed_comparison(dependencies):
    bots, templates, bindings, resolver, transport = dependencies
    templates.get_by_bot_id.side_effect = RuntimeError("private detail")
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["comparison"] == "older"
    assert result["restart_hint"] == "unknown"
    assert result["restart_reason"] == "policy_unknown"
    assert "private detail" not in str(result)


@pytest.mark.asyncio
async def test_independent_template_environment_override_cannot_attest_image(
    dependencies,
):
    bots, templates, bindings, resolver, transport = dependencies
    templates.get_by_bot_id.return_value = {
        "ext": {"envs": {"AGENTCLAW_IMAGE_VERSION": "REL20261009"}}
    }
    service = EngineVersionService(
        bots=bots,
        templates=templates,
        bindings=bindings,
        resolver=resolver,
        transport=transport,
        collaborators=MagicMock(),
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["detection"] == {
        "status": "read_failed",
        "reason": "source_unverified",
    }
    assert "current" not in result
    assert result["restart_hint"] == "unknown"


def test_target_is_loaded_once_from_the_backend_environment(monkeypatch):
    from injector import Injector
    from agentclaw.community.di.modules.engine_version_module import EngineVersionModule

    monkeypatch.setenv("TARGET_ENGINE_VERSION", "REL20261014")
    injector = Injector([EngineVersionModule()])
    assert injector.get(EngineVersionConfig).target_release == "REL20261014"
    monkeypatch.setenv("TARGET_ENGINE_VERSION", "REL20261015")
    assert injector.get(EngineVersionConfig).target_release == "REL20261014"
    monkeypatch.delenv("TARGET_ENGINE_VERSION")
    assert (
        Injector([EngineVersionModule()]).get(EngineVersionConfig).target_release == ""
    )
