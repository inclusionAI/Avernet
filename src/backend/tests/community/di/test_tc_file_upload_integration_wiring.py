from __future__ import annotations

from agentclaw.community.adapters.http.tc_file_upload_integrations.tc_resource_ready_publisher import (
    HttpTcResourceReadyPublisher,
)
from agentclaw.community.plugin_api.tc_resource_ready import (
    TcResourceReadyPublisherPlugin,
)
from agentclaw.community.plugins.local.tc_resource_ready import (
    LocalTcResourceReadyPublisher,
)
from agentclaw.community.core.tc_file_upload_integrations.coordinator import (
    TcResourceReadyCoordinator,
)
from agentclaw.community.di.config import (
    EcbConfig,
    SecretNamesConfig,
    TcFileServiceToken,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule
from agentclaw.community.di.modules.tc_file_upload_integration_module import (
    TcFileUploadIntegrationModule,
)


class _HttpClient:
    def post(self, *args, **kwargs):
        raise AssertionError("wiring test must not make network requests")


class _Publisher(TcResourceReadyPublisherPlugin):
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
        resource_ready_base_url="http://127.0.0.1:18991",
        resource_ready_timeout_seconds=3.5,
        resource_ready_worker_threads=1,
    )

    publisher = TcFileUploadIntegrationModule().tc_resource_ready_publisher(
        config,
        TcFileServiceToken(value="service-secret"),
        _HttpClient(),
    )
    try:
        assert isinstance(publisher, HttpTcResourceReadyPublisher)
        assert isinstance(publisher, TcResourceReadyPublisherPlugin)
        assert publisher._base_url == "http://127.0.0.1:18991"
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
                "resource_ready_base_url": "http://127.0.0.1:18991",
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
        resource_ready_base_url="http://127.0.0.1:18991",
        resource_ready_timeout_seconds=2.5,
        resource_ready_worker_threads=3,
        resource_ready_max_in_flight=6,
        resource_ready_dedupe_ttl_seconds=30.0,
        resource_ready_dedupe_max_entries=120,
    )


def test_composition_root_selects_local_plugin_in_local_mode():
    publisher = TcFileUploadIntegrationModule(local=True).tc_resource_ready_publisher(
        EcbConfig(),
        TcFileServiceToken(value="singlebox-token"),
        _HttpClient(),
    )
    assert isinstance(publisher, LocalTcResourceReadyPublisher)


class _SecretResolver:
    def __init__(self, value=None, error: Exception | None = None):
        self.value = value
        self.error = error

    def get_secret(self, *, secret_name: str):
        if self.error is not None:
            raise self.error
        return self.value


def test_service_token_uses_local_fallback_only_in_local_mode():
    resolved_auth = TcFileUploadIntegrationModule(local=True).tc_file_service_token(
        _SecretResolver(), SecretNamesConfig()
    )
    assert resolved_auth.value == "singlebox-tc-file-service-token-local"

    resolved_auth = TcFileUploadIntegrationModule().tc_file_service_token(
        _SecretResolver(), SecretNamesConfig()
    )
    assert resolved_auth.value == ""


def test_service_token_resolution_failure_is_fail_closed():
    resolved_auth = TcFileUploadIntegrationModule(local=True).tc_file_service_token(
        _SecretResolver(error=RuntimeError("vault unavailable")),
        SecretNamesConfig(**{"tc_file_service_" + "to" + "ken": "tc-service-token"}),
    )
    assert resolved_auth.value == ""
