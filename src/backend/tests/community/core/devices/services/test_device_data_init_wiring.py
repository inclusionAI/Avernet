"""Regression guard: the DataInitService provider must reach the
concrete device sub-services.

The DI migration removed the ``get_data_init_service()`` shim that
``DeviceService._trigger_data_init_on_device_ready`` called directly,
without threading a replacement. Result (silent, prod-only, untested):
every device reporting SUCCEEDED hit ``_data_init_service_provider is
None`` and skipped bot data-init.

This resolves the real DeviceService graph from the test injector and
asserts every routed sub-service has a working provider — exactly the
prod call path ``report_device_status`` -> ``_trigger_data_init...``
relies on.
"""
from agentclaw.community.core.bot_management.services.data_init_service import DataInitService
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.devices.services.device_service_router import (
    DeviceServiceRouter,
)


def test_data_init_provider_threaded_to_subservices(test_injector):
    router = test_injector.get(DeviceService)
    assert isinstance(router, DeviceServiceRouter)

    # Every sub-service the router can route report_device_status to
    # must have a non-None, callable provider that yields a real
    # DataInitService (not the silently-skipped None path).
    assert router._providers, "router has no sub-services"
    for name, svc in router._providers.items():
        provider = svc._data_init_service_provider
        assert provider is not None, f"{name}: data_init provider is None"
        assert isinstance(provider(), DataInitService), (
            f"{name}: provider did not yield a DataInitService"
        )
