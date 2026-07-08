"""HTTP-client concern — neutral binding (real httpx, all real deployments).

Scoped HTTP transport (``baas`` / ``bcn`` / ``general`` / ``masa_agent_eval``).
Every qualifier binds the real ``HttpxClient`` (the neutral shared impl at
``plugins/http_client.py``) with base_urls read from the neutral config and
env-aware ``pre``/``prod`` selection.

This module carries no profile-specific dependency, so corp and community share
it verbatim — it is installed in the profile-independent base list. The test
column's ``TestHttpClientModule`` installs *after* it and overrides these keys
with ``LocalHttpClient`` under pytest (and real httpx under singlebox), so it is
the one genuine profile-specific HTTP-client variant.
"""
from __future__ import annotations

from typing import Annotated

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_BAAS,
    QUALIFIER_BCN,
    QUALIFIER_GENERAL,
    QUALIFIER_MASA_AGENT_EVAL,
    HttpClient,
)
from agentclaw.community.plugins.http_client import HttpxClient
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class HttpClientModule(Module):
    """Real httpx clients scoped per upstream base_url (corp + community)."""

    @singleton
    @provider
    @inject
    def baas_http_client(
        self, baas: cfg.BaasConfig
    ) -> Annotated[HttpClient, QUALIFIER_BAAS]:
        api_base = (
            baas.api_base_url_pre
            if get_current_env() == "pre"
            else baas.api_base_url
        )
        logger.info("HttpClient[baas]: HttpxClient(base_url=%s)", api_base)
        return HttpxClient(base_url=api_base)

    @singleton
    @provider
    @inject
    def bcn_http_client(
        self, bcn: cfg.BcnConfig
    ) -> Annotated[HttpClient, QUALIFIER_BCN]:
        # BCN has separate pre/prod hosts; sending a pre provider token to the
        # prod host is rejected as an invalid provider admin token.
        base_url = bcn.base_url_pre if get_current_env() == "pre" else bcn.base_url
        logger.info("HttpClient[bcn]: HttpxClient(base_url=%s)", base_url)
        return HttpxClient(base_url=base_url)

    @singleton
    @provider
    def general_http_client(self) -> Annotated[HttpClient, QUALIFIER_GENERAL]:
        """``base_url=""`` — callers pass full absolute URLs."""
        logger.info("HttpClient[general]: HttpxClient(base_url='')")
        return HttpxClient(base_url="")

    @singleton
    @provider
    @inject
    def masa_agent_eval_http_client(
        self, config: cfg.MasaAgentEvalConfig
    ) -> Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL]:
        """MasaAgentEval API client — pre/prod URL selection."""
        base_url = (
            config.base_url_pre if get_current_env() == "pre" else config.base_url
        )
        logger.info("HttpClient[masa_agent_eval]: HttpxClient(base_url=%s)", base_url)
        return HttpxClient(base_url=base_url)
