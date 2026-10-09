"""Instance selection stays inside the authorized runtime binding."""

from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
)
from agentclaw.community.core.engine_runtime.stage import resolve_stage_device_context
from .test_relay import (
    BOT,
    OWNER,
    _BotService,
    _PublishRepo,
    _PublishRecord,
    _Resolver,
    _Transport,
    _relay,
)
from .test_connection import _svc, _Bots, _Bindings, _Devices


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,binding", [("online", 42), ("verify", 41)])
async def test_relay_pins_instance_after_stage_resolution(stage, binding):
    resolver = _Resolver()
    resolver.resolve_for_binding_invoke = Mock(
        wraps=resolver.resolve_for_binding_invoke
    )
    service = _relay(
        bot_service=_BotService({(BOT, OWNER): {"id": 100, "bot_type": "service"}}),
        resolver=resolver,
        publish_repo=_PublishRepo(
            {
                100: [
                    _PublishRecord(
                        {"binding": {"online": 42, "verify": 41}},
                        status="success" if stage == "online" else "validating",
                    )
                ]
            }
        ),
    )
    await service.call(
        bot_id=BOT,
        owner_id=OWNER,
        stage=stage,
        device_uuid="instance-B",
        method="GET",
        path="/api/sessions",
    )
    resolver.resolve_for_binding_invoke.assert_called_once_with(
        binding, OWNER, bot_id=BOT, device_uuid="instance-B"
    )


@pytest.mark.asyncio
async def test_unresolvable_instance_never_falls_back():
    resolver = _Resolver(raises=ConnInfoBuildError("wrong binding"))
    transport = _Transport()
    service = _relay(resolver=resolver, transport=transport)
    with pytest.raises(EngineDeviceNotReadyError):
        await service.call(
            bot_id=BOT,
            owner_id=OWNER,
            stage="draft",
            device_uuid="foreign-instance",
            method="GET",
            path="/api/sessions",
        )
    assert len(resolver.calls) == 1
    assert transport.calls == []


@pytest.mark.asyncio
async def test_non_pinning_provider_cannot_ignore_selector():
    transport = _Transport()
    with pytest.raises(EngineResourceNotFoundError):
        await _relay(transport=transport).call(
            bot_id=BOT,
            owner_id=OWNER,
            stage="draft",
            device_uuid="instance-B",
            method="GET",
            path="/api/sessions",
        )
    assert transport.calls == []


@pytest.mark.asyncio
async def test_desktop_baas_cannot_silently_ignore_instance():
    resolver = _Resolver()
    context = replace(resolver.resolve_for_bot(BOT, OWNER), provider="baas", bot_type="desktop")
    resolver.resolve_for_bot = Mock(return_value=context)
    transport = _Transport()
    with pytest.raises(EngineResourceNotFoundError):
        await _relay(resolver=resolver, transport=transport).call(
            bot_id=BOT, owner_id=OWNER, stage="draft", device_uuid="instance-B",
            method="GET", path="/api/sessions",
        )
    assert transport.calls == []


def test_connection_forwards_uuid_to_device_service():
    bindings = _Bindings()
    bindings.get_by_id = Mock(return_value=SimpleNamespace(device_provider="baas"))
    devices = _Devices()
    _svc(bots=_Bots(bot_type="service"), bindings=bindings, devices=devices).build(
        bot_id=BOT,
        owner_id=OWNER,
        caller_id=OWNER,
        stage="draft",
        device_uuid="instance-B",
    )
    assert devices.kwargs["binding_id"] == 42
    assert devices.kwargs["device_uuid"] == "instance-B"


def test_connection_rejects_unaddressable_instance_provider():
    bindings = _Bindings()
    bindings.get_by_id = Mock(return_value=SimpleNamespace(device_provider="local"))
    devices = _Devices()
    with pytest.raises(EngineResourceNotFoundError):
        _svc(bindings=bindings, devices=devices).build(
            bot_id=BOT,
            owner_id=OWNER,
            caller_id=OWNER,
            stage="draft",
            device_uuid="instance-B",
        )
    assert devices.kwargs is None


def test_file_stage_resolution_passes_uuid_without_changing_binding():
    resolver = Mock()
    bots = Mock()
    bots.get_by_id_and_owner.return_value = {"id": 100, "bot_type": "service"}
    publications = _PublishRepo({100: [_PublishRecord({"binding": {"online": 42}})]})
    resolve_stage_device_context(
        resolver,
        publications,
        Mock(),
        bots,
        bot_id=BOT,
        owner_id=OWNER,
        stage="online",
        device_uuid="instance-B",
    )
    resolver.resolve_for_binding.assert_called_once_with(
        42, OWNER, bot_id=BOT, device_uuid="instance-B"
    )
