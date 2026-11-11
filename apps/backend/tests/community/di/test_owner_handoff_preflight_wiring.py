"""The existing composition root resolves preflight through read-only ports."""

from agentclaw.community.api.owner_handoff_service import OwnerHandoffServiceProtocol
from agentclaw.community.core.bot_management.owner_handoff.preflight import OwnerHandoffPreflight
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.plugin_api.passport import PassportPlugin


def test_real_handoff_service_uses_the_configured_preflight_dependencies(test_injector):
    service = test_injector.get(OwnerHandoffServiceProtocol)
    assert isinstance(service._preflight, OwnerHandoffPreflight)
    assert service._preflight._passport is test_injector.get(PassportPlugin)
    assert service._preflight._bindings is test_injector.get(DeviceBindingRepository)
    assert service._preflight._publishes is test_injector.get(BotPublishRepositoryProtocol)
