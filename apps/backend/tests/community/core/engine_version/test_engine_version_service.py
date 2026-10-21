"""Release identity through the read-only Service API; only external seams fake."""

from types import SimpleNamespace
from functools import partial
from agentclaw.community.adapters.engine_release_probe import probe_engine_release
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
        "active_engine": "openclaw",
        "ext": {},
        "status": "ACTIVE",
    }
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
    return bots, bindings, resolver, transport


@pytest.mark.asyncio
async def test_owner_can_compare_current_engine_to_paired_backend_release(dependencies):
    bots, bindings, resolver, transport = dependencies
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
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
    bots, bindings, resolver, transport = dependencies
    transport.invoke.side_effect = failure
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
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

    bots, bindings, resolver, transport = dependencies
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
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
    bots, bindings, resolver, transport = dependencies
    bindings.get_active_by_bot_and_owner.return_value = None
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
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

    bots, bindings, resolver, transport = dependencies
    bindings.get_active_by_bot_and_owner.return_value.device_provider = "baas"
    inventory = MagicMock(return_value=[{"device_uuid": value} for value in ids])
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine", ["teclaw", "aicoding", "claude_code", "mystery", "", None]
)
async def test_other_engines_return_not_applicable_without_device_access(
    dependencies, engine
):
    bots, bindings, resolver, transport = dependencies
    bots.get_by_id_and_owner.return_value["active_engine"] = engine
    inventory = MagicMock(side_effect=AssertionError("must not list devices"))
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
        collaborators=MagicMock(),
        list_devices=inventory,
        config=EngineVersionConfig(""),
    )
    result = await service.inspect(bot_id="bot-1", owner_id="owner", caller_id="owner")
    assert result["detection"] == {
        "status": "not_applicable",
        "reason": "engine_not_supported",
    }
    assert result["comparison"] == "unknown"
    assert result["restart_hint"] == "restricted"
    assert "current" not in result
    assert "instance_id" not in result["context"]
    assert result["restart_reason"] == "engine_not_supported"
    bindings.get_active_by_bot_and_owner.assert_not_called()
    resolver.resolve_for_binding_invoke.assert_not_called()
    inventory.assert_not_called()
    transport.invoke.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize('space_visible,listed,operator', [(True, False, False), (False, True, False), (True, True, True)])
async def test_visible_advisory_uses_restart_standing_before_any_device_work(dependencies, space_visible, listed, operator):
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel

    class Rights:
        def get_operable_permission_level(self, **kwargs):
            return PermissionLevel.MEMBER if space_visible else PermissionLevel.NONE
        def get_permission_level(self, *args):
            return PermissionLevel.MEMBER if listed else PermissionLevel.NONE
        def get_explicit_permission_level(self, **kwargs):
            return PermissionLevel.MEMBER if operator else PermissionLevel.NONE

    bots, bindings, resolver, transport = dependencies
    service = EngineVersionService(
        lifecycle=MagicMock(),bots=bots, bindings=bindings, resolver=resolver,
        probe=partial(probe_engine_release, transport), collaborators=Rights(),
        list_devices=MagicMock(), config=EngineVersionConfig('REL20261014'))
    result = await service.inspect(bot_id='bot-1', owner_id='owner', caller_id='reader')
    if operator:
        assert result['restart_hint'] == 'recommended'
        transport.invoke.assert_awaited_once()
    else:
        assert result['detection'] == {'status': 'not_applicable', 'reason': 'restart_not_permitted'}
        assert result['restart_hint'] == 'restricted'
        assert 'current' not in result
        bindings.get_active_by_bot_and_owner.assert_not_called()
        resolver.resolve_for_binding_invoke.assert_not_called()
        transport.invoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('card_state,actions,allowed', [('service_draft', ['restart'], True), ('service_online', ['restart_publish'], False), ('service_draft', [], False)])
async def test_service_requires_existing_draft_restart_action(dependencies, card_state, actions, allowed):
    from agentclaw.community.core.bot_inventory.types import BotAction, DisplayState
    bots, bindings, resolver, transport = dependencies
    bots.get_by_id_and_owner.return_value['bot_type'] = 'service'
    lifecycle = SimpleNamespace(cards_for_bots=lambda **kwargs: {'bot-1': [SimpleNamespace(display_state=DisplayState(card_state), actions=[BotAction(action) for action in actions])]})
    service = EngineVersionService(bots=bots, bindings=bindings, resolver=resolver,
        probe=partial(probe_engine_release, transport), collaborators=MagicMock(), lifecycle=lifecycle,
        list_devices=MagicMock(), config=EngineVersionConfig('REL20261014'))
    result = await service.inspect(bot_id='bot-1', owner_id='owner', caller_id='owner')
    assert result['restart_hint'] == ('recommended' if allowed else 'restricted')
    assert transport.invoke.await_count == int(allowed)
