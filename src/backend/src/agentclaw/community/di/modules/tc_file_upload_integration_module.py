"""DI wiring for bounded TC resource-ready notifications."""

from __future__ import annotations

from typing import Annotated

from injector import Module, inject, provider, singleton

from agentclaw.community.adapters.http.tc_file_upload_integrations.tc_resource_ready_publisher import (
    HttpTcResourceReadyPublisher,
)
from agentclaw.community.api.tc_resource_ready_observer import (
    TcResourceReadyObserverProtocol,
)
from agentclaw.community.core.ports.tc_resource_ready_port import (
    TcResourceReadyPublisherPort,
)
from agentclaw.community.core.tc_file_upload_integrations.coordinator import (
    TcResourceReadyCoordinator,
)
from agentclaw.community.di.config import EcbConfig
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.utils.env_utils import get_current_env


class TcFileUploadIntegrationModule(Module):
    @singleton
    @provider
    @inject
    def tc_resource_ready_publisher(
        self,
        ecb_config: EcbConfig,
        http_client: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> TcResourceReadyPublisherPort:
        base_url = (
            ecb_config.base_url_pre
            if get_current_env() == "pre"
            else ecb_config.base_url
        )
        return HttpTcResourceReadyPublisher(
            base_url=base_url,
            http_client=http_client,
            timeout_seconds=ecb_config.resource_ready_timeout_seconds,
            worker_threads=ecb_config.resource_ready_worker_threads,
        )

    @singleton
    @provider
    @inject
    def tc_resource_ready_observer(
        self,
        publisher: TcResourceReadyPublisherPort,
        ecb_config: EcbConfig,
    ) -> TcResourceReadyObserverProtocol:
        return TcResourceReadyCoordinator(
            publisher=publisher,
            max_in_flight=ecb_config.resource_ready_max_in_flight,
            dedupe_ttl_seconds=ecb_config.resource_ready_dedupe_ttl_seconds,
            dedupe_max_entries=ecb_config.resource_ready_dedupe_max_entries,
        )
