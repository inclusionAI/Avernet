from __future__ import annotations

from agentclaw.community.adapters.http.tc_file_upload_integrations.tc_resource_ready_publisher import (
    HttpTcResourceReadyPublisher,
)
from agentclaw.community.core.ports.tc_resource_ready_port import (
    TcResourceReadyPublisherPort,
)
from agentclaw.community.core.tc_file_upload_integrations.coordinator import (
    TcResourceReadyCoordinator,
)
from agentclaw.community.di.config import EcbConfig
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule
from agentclaw.community.di.modules.tc_file_upload_integration_module import (
    TcFileUploadIntegrationModule,
)


class _HttpClient:
    def post(self, *args, **kwargs):
        raise AssertionError("wiring test must not make network requests")


class _Publisher(TcResourceReadyPublisherPort):
    async def publish(self, event) -> None:
        return None


def test_composition_root_builds_the_http_adapter_with_typed_limits(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.di.modules.tc_file_upload_integration_module.get_current_env",
        lambda: "pre",
    )
    config = EcbConfig(
        base_url="https://prod.example",
        base_url_pre="https://pre.example",
        resource_ready_timeout_seconds=3.5,
        resource_ready_worker_threads=1,
    )

    publisher = TcFileUploadIntegrationModule().tc_resource_ready_publisher(
        config,
        _HttpClient(),
    )
    try:
        assert isinstance(publisher, HttpTcResourceReadyPublisher)
        assert isinstance(publisher, TcResourceReadyPublisherPort)
        assert publisher._base_url == "https://pre.example"
        assert publisher._timeout_seconds == 3.5
        assert publisher._executor._max_workers == 1
    finally:
        publisher._executor.shutdown(wait=True)


def test_composition_root_builds_the_bounded_observer():
    config = EcbConfig(
        resource_ready_max_in_flight=4,
        resource_ready_dedupe_ttl_seconds=15.0,
        resource_ready_dedupe_max_entries=21,
    )

    observer = TcFileUploadIntegrationModule().tc_resource_ready_observer(
        _Publisher(),
        config,
    )

    assert isinstance(observer, TcResourceReadyCoordinator)
    assert observer._max_in_flight == 4
    assert observer._dedupe_ttl_seconds == 15.0
    assert observer._dedupe_max_entries == 21


def test_ecb_config_provider_loads_resource_ready_limits(monkeypatch):
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {
            "ecb": {
                "base_url": "https://ecb.example",
                "resource_ready_timeout_seconds": 2.5,
                "resource_ready_worker_threads": 3,
                "resource_ready_max_in_flight": 6,
                "resource_ready_dedupe_ttl_seconds": 30.0,
                "resource_ready_dedupe_max_entries": 120,
            }
        },
    )

    config = ConfigModule().ecb()

    assert config == EcbConfig(
        base_url="https://ecb.example",
        resource_ready_timeout_seconds=2.5,
        resource_ready_worker_threads=3,
        resource_ready_max_in_flight=6,
        resource_ready_dedupe_ttl_seconds=30.0,
        resource_ready_dedupe_max_entries=120,
    )
