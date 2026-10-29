"""Compose best-effort manager projection using direct, self-signed BCS HTTP.

Disabled deployments resolve no network resources. Enabled deployments need only
an explicit endpoint/env and the existing principal signing key; Redis is not a
dependency. Runtime state and retry ownership stay in the bounded dispatcher.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from injector import Binder, Module, provider, singleton

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher, DisabledBotManagerSync,
)
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle, DisabledBotManagerSyncLifecycle,
)
from agentclaw.community.core.bot_collaborator.manager_sync.repair import (
    ManagerSyncRepairService, DisabledManagerSyncRepair,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import BotManagerSyncService
from agentclaw.community.core.repository.implementations.bot.manager_sync import SqlManagerSyncReadRepository
from agentclaw.community.core.repository.protocols.bot.manager_sync import ManagerSyncReadRepository
from agentclaw.community.di.bot_manager_sync_config import (
    CONFIG_BLOCK_NAME, ManagerSyncConfig, parse_manager_sync_config,
)
from agentclaw.community.di.config import SecretNamesConfig
from agentclaw.community.di.modules.config_module import read_user_config
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.gateway_principal_config import (
    bcs_service_principal_signer_config, resolve_signing_key,
)

logger = get_logger()


@dataclass(frozen=True)
class ManagerSyncStack:
    """An available delivery plugin, or a reason the feature cannot run."""

    bcs: BcsManagersPlugin | None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.bcs is not None

    @classmethod
    def available_with(cls, bcs: BcsManagersPlugin) -> ManagerSyncStack:
        return cls(bcs=bcs)

    @classmethod
    def unavailable(cls, reason: str) -> ManagerSyncStack:
        return cls(bcs=None, reason=reason)


def _build_principal_minter(
    resolver: SecretResolver, secret_names: SecretNamesConfig
) -> Callable[[str], str] | None:
    """Resolve the delivery signing key and close a mint over it.

    Returns a tenant → token callable minting **this component's**
    service identity under the fixed BCS envelope
    (``core/gateway_principal/service_principal.py``), or ``None`` when
    the shared signing key did not resolve — the caller then binds the
    feature unavailable, because a deployment that cannot mint a
    principal BCS will verify is exactly one that must not send at all.

    The key is resolved under the same secret name and conventions the
    HTTP boot's verifier init uses (:func:`resolve_signing_key`), so the
    deployments that can mint are exactly the ones that can verify. The
    callable closes over the signer config built from the resolved key
    rather than the boot-installed global: the credential the stack
    validated is the credential the sender mints with.
    """
    from agentclaw.community.core.gateway_principal import (
        mint_service_app_principal,
    )

    signing_key = resolve_signing_key(
        resolver, secret_names.gateway_principal_signing_key
    )
    if not signing_key:
        logger.warning(
            "[MANAGER-SYNC] no principal signing key resolved under "
            "secret_names.gateway_principal_signing_key=%r — cannot mint "
            "BCS service principals, so no manager sync can be delivered "
            "by this deployment",
            secret_names.gateway_principal_signing_key,
        )
        return None
    signer = bcs_service_principal_signer_config(signing_key)

    def mint_principal(tenant: str) -> str:
        return mint_service_app_principal(signer, tenant)

    return mint_principal


class BotManagerSyncModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ManagerSyncReadRepository, to=SqlManagerSyncReadRepository, scope=singleton)

    @singleton
    @provider
    def manager_sync_config(self) -> ManagerSyncConfig:
        return parse_manager_sync_config(read_user_config().get(CONFIG_BLOCK_NAME))

    @singleton
    @provider
    def manager_sync_stack(self) -> ManagerSyncStack:
        config = self.__injector__.get(ManagerSyncConfig)
        if not config.enabled:
            return ManagerSyncStack.unavailable("disabled")
        mint_principal = _build_principal_minter(
            self.__injector__.get(SecretResolver), self.__injector__.get(SecretNamesConfig)
        )
        if mint_principal is None:
            return ManagerSyncStack.unavailable("principal-signing-key-required")
        from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin

        return ManagerSyncStack.available_with(HttpBcsManagersPlugin(
            base_url=config.bcs_base_url, mint_principal=mint_principal
        ))

    @singleton
    @provider
    def manager_sync(self) -> BotManagerSyncProtocol:
        config = self.__injector__.get(ManagerSyncConfig)
        if not config.enabled:
            return DisabledBotManagerSync()
        stack = self.__injector__.get(ManagerSyncStack)
        if stack.bcs is None:
            logger.error("[MANAGER-SYNC] syncer unavailable: reason=%s", stack.reason)
            return DisabledBotManagerSync()
        reader = self.__injector__.get(ManagerSyncReadRepository)
        return BotManagerSyncDispatcher(
            BotManagerSyncService(reader=reader, bcs=stack.bcs), reader, env=config.env
        )

    @singleton
    @provider
    def manager_sync_lifecycle(self) -> BotManagerSyncLifecycle:
        entry = self.__injector__.get(BotManagerSyncProtocol)
        if not isinstance(entry, BotManagerSyncDispatcher):
            return DisabledBotManagerSyncLifecycle()
        stack = self.__injector__.get(ManagerSyncStack)
        assert stack.bcs is not None  # same singleton stack that built the dispatcher
        return BotManagerSyncLifecycle(dispatcher=entry, bcs=stack.bcs)

    @singleton
    @provider
    def manager_sync_repair(self) -> ManagerSyncRepairService:
        entry = self.__injector__.get(BotManagerSyncProtocol)
        if not isinstance(entry, BotManagerSyncDispatcher):
            return DisabledManagerSyncRepair(reason="disabled-or-unavailable")
        return ManagerSyncRepairService(
            reader=self.__injector__.get(ManagerSyncReadRepository), notifier=entry
        )


__all__ = ["BotManagerSyncModule", "ManagerSyncStack"]
