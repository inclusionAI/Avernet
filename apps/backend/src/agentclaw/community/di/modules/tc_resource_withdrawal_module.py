"""Composition root for durable single-chat withdrawal; no implicit local success."""

from dataclasses import fields
from typing import Annotated

from injector import Module, provider, singleton

from agentclaw.community.core.repository.implementations.platform.resource_withdrawal import (
    ResourceWithdrawalRepository,
)
from agentclaw.community.core.repository.protocols.platform import (
    ResourceWithdrawalRepositoryProtocol,
)
from agentclaw.community.core.session_resources.withdrawal_worker import (
    ResourceWithdrawalWorker,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.tc_resource_withdrawal_config import (
    ResourceWithdrawalConfig,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_GENERAL
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    ResourceWithdrawalPublisherPlugin,
)
from agentclaw.community.plugins.community.tc_resource_withdrawal import (
    HttpResourceWithdrawalPublisher,
)
from agentclaw.community.plugins.local.tc_resource_withdrawal import (
    LocalResourceWithdrawalPublisher,
)


class ResourceWithdrawalModule(Module):
    def __init__(self, *, local: bool = False) -> None:
        self._local = local

    @singleton
    @provider
    def config(self) -> ResourceWithdrawalConfig:
        raw = config_module.read_user_config().get("tc_resource_withdrawal", {})
        if not isinstance(raw, dict):
            raise ValueError("tc_resource_withdrawal_must_be_mapping")
        if set(raw) - {f.name for f in fields(ResourceWithdrawalConfig)}:
            raise ValueError("tc_resource_withdrawal_unknown_keys")
        return ResourceWithdrawalConfig(**raw)

    @singleton
    @provider
    def repository(self, db: DatabasePlugin) -> ResourceWithdrawalRepositoryProtocol:
        return ResourceWithdrawalRepository(db)

    @singleton
    @provider
    def publisher(
        self,
        config: ResourceWithdrawalConfig,
        secret_resolver: SecretResolver,
        http_client: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> ResourceWithdrawalPublisherPlugin:
        if self._local:
            return LocalResourceWithdrawalPublisher()
        token = ""
        if config.enabled:
            try:
                resolved = secret_resolver.get_secret(secret_name=config.secret_name)
            except Exception:
                raise ValueError("withdrawal_secret_resolution_failed") from None
            token = getattr(
                resolved,
                "secret_value",
                None,
            )
            if (
                not isinstance(token, str)
                or not token.strip()
                or token != token.strip()
                or any(ord(c) < 32 for c in token)
            ):
                raise ValueError("withdrawal_secret_missing_or_invalid")
        return HttpResourceWithdrawalPublisher(
            base_url=config.base_url,
            authorization_value=token,
            http_client=http_client,
            timeout_seconds=config.timeout_seconds,
        )

    @singleton
    @provider
    def worker(
        self,
        repository: ResourceWithdrawalRepositoryProtocol,
        publisher: ResourceWithdrawalPublisherPlugin,
        config: ResourceWithdrawalConfig,
    ) -> ResourceWithdrawalWorker:
        return ResourceWithdrawalWorker(repository, publisher, config)
