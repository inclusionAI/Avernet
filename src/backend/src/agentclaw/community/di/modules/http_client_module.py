"""HTTP-client concern — neutral binding (real httpx, all real deployments).

Scoped HTTP transport (``baas`` / ``bcn`` / ``general`` / ``masa_agent_eval``).
Every qualifier binds the real ``HttpxClient`` (the neutral shared impl at
``plugins/http_client.py``) with base_urls read from the neutral config and
env-aware ``pre``/``prod`` selection.

This module carries no profile-specific dependency, so corp and community share
it verbatim — it is installed in the profile-independent base list. Only the
``test`` and ``corp_test`` columns install ``TestHttpClientModule`` after it to
override these keys with no-network ``LocalHttpClient`` doubles. ``singlebox``
deliberately consumes these real HTTP clients to reach its local services.
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


def _log_binding(qualifier: str, base_url: str, policy: cfg.HttpClientPoolPolicy) -> None:
    """Record one binding's *resolved* transport policy at boot.

    This is the only place a deployment can see what an override actually
    resolved to, so it doubles as the check for a mistyped ``overrides`` key
    (which is inert by design) and as confirmation that an ``http2`` flip took
    effect in a pre environment.
    """
    logger.info(
        "HttpClient[%s]: HttpxClient(base_url=%s, max_connections=%d, "
        "max_keepalive=%d, keepalive_expiry=%s, http2=%s)",
        qualifier,
        base_url or "''",
        policy.max_connections,
        policy.max_keepalive_connections,
        policy.keepalive_expiry,
        policy.http2,
    )


def _client(base_url: str, policy: cfg.HttpClientPoolPolicy) -> HttpxClient:
    """Build one pooled client from a resolved policy."""
    return HttpxClient(
        base_url=base_url,
        max_connections=policy.max_connections,
        max_keepalive_connections=policy.max_keepalive_connections,
        keepalive_expiry=policy.keepalive_expiry,
        http2=policy.http2,
    )


class HttpClientModule(Module):
    """Real httpx clients scoped per upstream base_url (corp + community).

    Each binding resolves its own transport policy from
    ``HttpClientPoolConfig`` — shared defaults unless an override names that
    qualifier — so the four upstreams can be tuned independently. Every provider
    passes its *own* ``QUALIFIER_*`` constant, the same one its return type is
    annotated with, so an override key cannot drift from the injector key.
    """

    @singleton
    @provider
    @inject
    def baas_http_client(
        self, baas: cfg.BaasConfig, pool: cfg.HttpClientPoolConfig
    ) -> Annotated[HttpClient, QUALIFIER_BAAS]:
        api_base = (
            baas.api_base_url_pre
            if get_current_env() == "pre"
            else baas.api_base_url
        )
        policy = pool.for_qualifier(QUALIFIER_BAAS)
        _log_binding(QUALIFIER_BAAS, api_base, policy)
        return _client(api_base, policy)

    @singleton
    @provider
    @inject
    def bcn_http_client(
        self, bcn: cfg.BcnConfig, pool: cfg.HttpClientPoolConfig
    ) -> Annotated[HttpClient, QUALIFIER_BCN]:
        # BCN has separate pre/prod hosts; sending a pre provider token to the
        # prod host is rejected as an invalid provider admin token.
        base_url = bcn.base_url_pre if get_current_env() == "pre" else bcn.base_url
        policy = pool.for_qualifier(QUALIFIER_BCN)
        _log_binding(QUALIFIER_BCN, base_url, policy)
        return _client(base_url, policy)

    @singleton
    @provider
    @inject
    def general_http_client(
        self, pool: cfg.HttpClientPoolConfig
    ) -> Annotated[HttpClient, QUALIFIER_GENERAL]:
        """``base_url=""`` — callers pass full absolute URLs.

        This binding's pool spans every host its callers address (the agentclaw
        proxy, LLM endpoints, container IPs), and ``max_connections`` is a
        pool-wide budget rather than per-origin — so it is the binding most
        likely to want an override.
        """
        policy = pool.for_qualifier(QUALIFIER_GENERAL)
        _log_binding(QUALIFIER_GENERAL, "", policy)
        return _client("", policy)

    @singleton
    @provider
    @inject
    def masa_agent_eval_http_client(
        self, config: cfg.MasaAgentEvalConfig, pool: cfg.HttpClientPoolConfig
    ) -> Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL]:
        """MasaAgentEval API client — pre/prod URL selection."""
        base_url = (
            config.base_url_pre if get_current_env() == "pre" else config.base_url
        )
        policy = pool.for_qualifier(QUALIFIER_MASA_AGENT_EVAL)
        _log_binding(QUALIFIER_MASA_AGENT_EVAL, base_url, policy)
        return _client(base_url, policy)
