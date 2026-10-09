"""HTTP-client concern — fixed no-network test bindings."""
from __future__ import annotations

from typing import Annotated

from injector import Module, provider, singleton

from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_BAAS,
    QUALIFIER_BCN,
    QUALIFIER_GENERAL,
    QUALIFIER_MASA_AGENT_EVAL,
    HttpClient,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


class TestHttpClientModule(Module):
    """No-network HTTP clients for test and corp_test profiles."""

    @singleton
    @provider
    def baas_http_client(self) -> Annotated[HttpClient, QUALIFIER_BAAS]:
        return LocalHttpClient(base_url="http://localhost:8890")

    @singleton
    @provider
    def bcn_http_client(self) -> Annotated[HttpClient, QUALIFIER_BCN]:
        return LocalHttpClient(base_url="http://localhost:8891")

    @singleton
    @provider
    def general_http_client(self) -> Annotated[HttpClient, QUALIFIER_GENERAL]:
        return LocalHttpClient(base_url="")

    @singleton
    @provider
    def masa_agent_eval_http_client(
        self,
    ) -> Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL]:
        return LocalHttpClient(base_url="http://localhost:8080")
