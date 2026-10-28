"""Device resources keep their entity while authority follows the current Bot."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.device_instance_service import DeviceInstanceService
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401


@pytest.fixture(params=["u1", "staff_u1"])
def device(world, request):
    entity = request.param
    bots = world.get(BotRepository)
    bots.insert({
        "bot_id": "device-source", "bot_name": "Device source", "owner_id": "new-owner",
        "entity_id": entity, "entity_type": "staff", "creator_id": "u1",
        "bot_type": "service", "active_engine": "openclaw", "status": "ACTIVE",
        "ext": {"passport": {"agent_code": "test-agent"}},
    })
    bindings = world.get(DeviceBindingRepository)
    binding_id = bindings.insert_binding(
        entity_id=entity, entity_type="staff", device_id="DEVICE-handoff",
        device_provider="baas", env=get_current_env(), status="ACTIVE",
        device_props={"bolt_id": "device-source"}, apply_reason="test", applied_by="u1",
    )
    return bots, bindings, binding_id, entity


@pytest.mark.parametrize("locator_kind", ["entity", "owner"])
def test_bootstrap_resolves_bot_then_queries_passport_under_immutable_entity(device, locator_kind):
    bots, bindings, _, entity = device
    provider = Mock()
    provider.update_device_headers.return_value = True
    passport = Mock()
    passport.query_token.return_value = "test-token"
    router = DeviceServiceRouter(
        repository=bindings, bot_query=bots, bot_repo=bots,
        providers={"baas": provider}, default_provider_key="baas", passport_plugin=passport,
    )

    result = router.bootstrap_device_auth(
        device_id="DEVICE-handoff", bot_id="device-source",
        owner_id=entity if locator_kind == "entity" else "new-owner",
    )

    assert result["agent_code"] == "test-agent"
    passport.query_token.assert_called_once_with("device-source", entity)
    allocated = provider.update_device_headers.call_args.kwargs["device"]
    assert allocated.device_props["entity_id"] == entity


@pytest.mark.parametrize("actor,allowed", [("new-owner", True), ("u1", False), ("outsider", False)])
def test_instance_restart_authorizes_current_owner_not_binding_entity(device, actor, allowed):
    bots, bindings, binding_id, _ = device
    baas = Mock()
    baas.restart_devices.return_value = {"publish_id": 99}
    provider = Mock()
    provider._baas_service = baas
    collaborators = Mock()
    collaborators.check_collaborator_permission.return_value = {"has_permission": False}
    service = DeviceInstanceService(
        repository=bindings, providers={"baas": provider}, publish_repo=None,
        bot_repo=bots, collaborator_service_factory=lambda: collaborators,
    )
    operator = OperatorContext(
        staff_id=actor, staff=actor, nick_name=actor, operator_name=actor, tenant_id="default",
    )
    if allowed:
        assert service.restart_device(binding_id=binding_id, device_uuid="DEVICE-handoff", operator=operator) == {"publish_id": 99}
        baas.restart_devices.assert_called_once()
    else:
        with pytest.raises(InvalidDeviceStatusError):
            service.restart_device(binding_id=binding_id, device_uuid="DEVICE-handoff", operator=operator)
        baas.restart_devices.assert_not_called()
