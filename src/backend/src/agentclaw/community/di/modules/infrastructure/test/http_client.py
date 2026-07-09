"""HTTP-client concern — test / singlebox binding.

pytest-mock / real-local split on ``SERVER_ENV``: singlebox is a real local
micro-service launch (hits the local BaaS at ``application-singlebox.yaml``'s
``baas.api_base_url``), so it must send real httpx. pytest uses
``LocalHttpClient`` which raises on un-stubbed calls so the suite never reaches
the network.
"""
from __future__ import annotations

import os
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
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class TestHttpClientModule(Module):
    """test / singlebox: pytest-mock / real-local split on ``SERVER_ENV``."""

    @singleton
    @provider
    @inject
    def baas_http_client(
        self, baas: cfg.BaasConfig
    ) -> Annotated[HttpClient, QUALIFIER_BAAS]:
        if (os.getenv("SERVER_ENV") or "").lower() == "singlebox":
            from agentclaw.community.plugins.http_client import HttpxClient

            logger.info(
                "HttpClient[baas]: HttpxClient(base_url=%s) (singlebox)",
                baas.api_base_url,
            )
            return HttpxClient(base_url=baas.api_base_url)

        from agentclaw.community.plugins.local.http_client import LocalHttpClient

        logger.info("HttpClient[baas]: LocalHttpClient (pytest mock)")
        return LocalHttpClient(base_url="http://localhost:8890")

    @singleton
    @provider
    @inject
    def bcn_http_client(
        self, bcn: cfg.BcnConfig
    ) -> Annotated[HttpClient, QUALIFIER_BCN]:
        if (os.getenv("SERVER_ENV") or "").lower() == "singlebox":
            from agentclaw.community.plugins.http_client import HttpxClient

            base_url = bcn.base_url_pre if get_current_env() == "pre" else bcn.base_url

            logger.info(
                "HttpClient[bcn]: HttpxClient(base_url=%s) (singlebox)", base_url
            )
            return HttpxClient(base_url=base_url)

        from agentclaw.community.plugins.local.http_client import LocalHttpClient

        logger.info("HttpClient[bcn]: LocalHttpClient (pytest mock)")
        return LocalHttpClient(base_url="http://localhost:8891")

    @singleton
    @provider
    def general_http_client(self) -> Annotated[HttpClient, QUALIFIER_GENERAL]:
        if (os.getenv("SERVER_ENV") or "").lower() == "singlebox":
            from agentclaw.community.plugins.http_client import HttpxClient

            logger.info("HttpClient[general]: HttpxClient(base_url='') (singlebox)")
            return HttpxClient(base_url="")

        from agentclaw.community.plugins.local.http_client import LocalHttpClient

        logger.info("HttpClient[general]: LocalHttpClient (pytest mock)")
        return LocalHttpClient(base_url="")

    @singleton
    @provider
    @inject
    def masa_agent_eval_http_client(
        self, config: cfg.MasaAgentEvalConfig
    ) -> Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL]:
        """MasaAgentEval API client — singlebox uses real client, pytest uses mock."""
        if (os.getenv("SERVER_ENV") or "").lower() == "singlebox":
            # B11: community HttpxClient (corp-free). The baas/bcn clients in this
            # same module already use it; switching the masa client off the corp
            # HttpxClient makes this module carry no ``agentclaw.corp`` import, so it
            # is safe in the corp-free test/singlebox column.
            from agentclaw.community.plugins.http_client import HttpxClient
            from agentclaw.community.utils.env_utils import get_current_env

            base_url = config.base_url_pre if get_current_env() == "pre" else config.base_url
            logger.info(
                "HttpClient[masa_agent_eval]: HttpxClient(base_url=%s) (singlebox)",
                base_url,
            )
            return HttpxClient(base_url=base_url)

        from agentclaw.community.plugins.local.http_client import LocalHttpClient

        logger.info("HttpClient[masa_agent_eval]: LocalHttpClient (pytest mock)")
        return LocalHttpClient(base_url="http://localhost:8080")
