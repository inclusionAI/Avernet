"""The locator is composed once with the same runtime and device services."""
from agentclaw.community.core.runtime_binding.container_locator import RuntimeContainerLocator
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.service_bot.services.baas_service import BaasService


def test_runtime_container_locator_uses_registered_singletons(test_injector):
    locator = test_injector.get(RuntimeContainerLocator)
    assert locator is test_injector.get(RuntimeContainerLocator)
    assert locator._runtime_bindings is test_injector.get(RuntimeBindingResolutionService)
    assert locator._baas_service is test_injector.get(BaasService)
