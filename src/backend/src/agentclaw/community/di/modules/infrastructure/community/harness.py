"""Harness LLM concern — community-only environment-backed configuration."""

from __future__ import annotations

from typing import Annotated

from injector import Module, inject, provider, singleton

from agentclaw.community.core.harness.services.llm import LLM
from agentclaw.community.di.config_community import CommunityLLMHarnessConfig
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


class CommunityHarnessModule(Module):
    """Override only the community profile's shared Harness LLM binding."""

    @singleton
    @provider
    def llm_config(self) -> CommunityLLMHarnessConfig:
        from agentclaw.community.di.modules.config_module import _block

        block = _block("llm")
        defaults = CommunityLLMHarnessConfig()
        raw_timeout_ms = block.get("timeout_ms", defaults.timeout_ms)
        try:
            timeout_ms = int(raw_timeout_ms)
        except (TypeError, ValueError):
            # Golden snapshots inspect YAML before environment expansion.
            if (
                isinstance(raw_timeout_ms, str)
                and raw_timeout_ms.startswith("${")
                and raw_timeout_ms.endswith("}")
            ):
                timeout_ms = defaults.timeout_ms
            else:
                raise ValueError(
                    f"llm.timeout_ms must be an integer, got {raw_timeout_ms!r}"
                ) from None
        return CommunityLLMHarnessConfig(
            base_url=block.get("base_url", defaults.base_url),
            secret_name=block.get("secret_name", defaults.secret_name),
            model=block.get("model", defaults.model),
            timeout_ms=timeout_ms,
        )

    @singleton
    @provider
    @inject
    def llm(
        self,
        config: CommunityLLMHarnessConfig,
        secret_resolver: SecretResolver,
        general_http: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> LLM:
        # The shared client historically owns the ``/v1/chat/completions``
        # suffix. Normalize the OpenAI-style community base URL here so the
        # shared implementation — also used by internal production — stays
        # byte-for-byte unchanged.
        base_url = config.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        return LLM(
            base_url=base_url,
            secret_name=config.secret_name,
            secret_resolver=secret_resolver,
            http_client=general_http,
            model=config.model,
            timeout_ms=config.timeout_ms,
        )
