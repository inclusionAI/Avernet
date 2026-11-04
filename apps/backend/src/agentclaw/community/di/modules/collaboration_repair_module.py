"""Request-scoped collaboration repair composition.

Each request owns fresh delivery plugins on its event loop and closes them
in finally; background clients must not be borrowed across loops. Deployment
prerequisites and the single BCN timeout budget are documented in the Core
module README. Unavailable deployments refuse before creating plugins.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from injector import Binder, Module, provider, singleton

from agentclaw.community.core.bot_management.collaboration_repair.service import (
    CollaborationRepairService,
)
from agentclaw.community.core.bot_management.collaboration_repair.service_protocol import (
    CollaborationRepairServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnService,
    _get_provider_config,
)
from agentclaw.community.core.repository.implementations.bot.collaboration_repair import (
    SqlCollaborationRepairReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.collaboration_repair import (
    CollaborationRepairReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.plugins.community.bcs_collaboration_metadata import (
    BcnCollaborationMetadataAdapter,
)
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.di.config import BcnConfig, SecretNamesConfig
from agentclaw.community.di.modules.bot_manager_sync_module import (
    _build_principal_minter,
)
from agentclaw.community.kernel.collaboration_repair import (
    RepairPreconditionError,
    RepairPreconditionErrorCode,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipPlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

Clock = Callable[[], float]


async def _close_quietly(
    plugin: BcsCollaborationMetadataPlugin | BcsOwnershipPlugin | BcsManagersPlugin,
) -> None:
    """Close one request-owned plugin without stranding the remaining clients.

    Log only types: credentials and transport details must not escape cleanup.
    """
    try:
        await plugin.aclose()
    except Exception as exc:  # noqa: BLE001 — one close must not strand the rest
        logger.warning(
            "[COLLABORATION-REPAIR] plugin close failed: plugin=%s error_type=%s",
            type(plugin).__name__,
            type(exc).__name__,
        )


class CollaborationRepairPlugins(Protocol):
    """The per-request delivery-plugin source bound at composition time.

    ``available`` is False exactly when this deployment must not build a
    plugin or send anything; ``reason`` then carries the diagnostic token
    the factory logs. Implementations build fresh plugin instances on every
    call — the ownership/managers HTTP plugins own a client per request.
    """

    @property
    def available(self) -> bool:
        """Whether this deployment can deliver a repair at all."""
        ...

    @property
    def reason(self) -> str:
        """The diagnostic token for an unavailable deployment."""
        ...

    def metadata(
        self, profile_headers: Mapping[str, str]
    ) -> BcsCollaborationMetadataPlugin:
        """One metadata adapter carrying THIS request's credentials."""
        ...

    def ownership(self) -> BcsOwnershipPlugin:
        """A fresh ownership plugin for the caller's loop."""
        ...

    def managers(self) -> BcsManagersPlugin:
        """A fresh managers plugin for the caller's loop."""
        ...


@dataclass(frozen=True)
class HttpCollaborationRepairPlugins(CollaborationRepairPlugins):
    """The production source: BCN metadata + direct HTTPS to BCS.

    ``bcn_service``/``mint_principal`` are ``None`` only in the unavailable
    posture (``unavailable()``), mirroring ``ManagerSyncStack``'s optional
    delivery plugin: the value then describes why nothing may be sent instead
    of pretending a capability the deployment does not have.
    """

    bcn_service: BcnService | None = None
    bcs_base_url: str = ""
    mint_principal: Callable[[str], str] | None = None
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.bcn_service is not None and self.mint_principal is not None

    @property
    def reason(self) -> str:
        return self.unavailable_reason

    @classmethod
    def unavailable(cls, reason: str) -> HttpCollaborationRepairPlugins:
        return cls(unavailable_reason=reason)

    def metadata(
        self, profile_headers: Mapping[str, str]
    ) -> BcsCollaborationMetadataPlugin:
        bcn_service, _ = self._collaborators()
        return BcnCollaborationMetadataAdapter(bcn_service, profile_headers)

    def ownership(self) -> BcsOwnershipPlugin:
        from agentclaw.community.plugins.community.bcs_ownership import (
            HttpBcsOwnershipPlugin,
        )

        _, mint_principal = self._collaborators()
        return HttpBcsOwnershipPlugin(
            base_url=self.bcs_base_url, mint_principal=mint_principal
        )

    def managers(self) -> BcsManagersPlugin:
        from agentclaw.community.plugins.community.bcs_managers import (
            HttpBcsManagersPlugin,
        )

        _, mint_principal = self._collaborators()
        return HttpBcsManagersPlugin(
            base_url=self.bcs_base_url, mint_principal=mint_principal
        )

    def _collaborators(self) -> tuple[BcnService, Callable[[str], str]]:
        """The collaborators, or a loud failure (the factory refuses first)."""
        bcn_service, mint_principal = self.bcn_service, self.mint_principal
        if bcn_service is None or mint_principal is None:
            raise RuntimeError(
                "collaboration repair delivery unavailable: "
                + (self.unavailable_reason or "unknown")
            )
        return bcn_service, mint_principal


class CollaborationRepairFactory:
    """Open one repair service per request, owning that request's plugins.

    The factory is long-lived (it holds the read-only repositories and the
    typed sync config); the delivery plugins are not. ``open`` refuses on an
    unavailable deployment before constructing anything, and on success
    closes every plugin it built in ``finally`` — including a partially
    constructed set.
    """

    def __init__(
        self,
        plugins: CollaborationRepairPlugins,
        reader: CollaborationRepairReadRepository,
        ownership_reader: OwnershipSyncReadRepository,
        managers_reader: ManagerSyncReadRepository,
        config: ManagerSyncConfig,
        *,
        timeout_seconds: float,
    ) -> None:
        self._plugins = plugins
        self._reader = reader
        self._ownership_reader = ownership_reader
        self._managers_reader = managers_reader
        self._config = config
        self._timeout_seconds = timeout_seconds

    def _refusal_reason(self) -> str:
        """Why this deployment cannot repair, or ``""`` when it can."""
        if not self._config.enabled:
            return "disabled"
        if not self._config.ownership_enabled:
            # Ownership is a fixed repair component; a managers-only
            # deployment cannot run a request that would leave it out.
            return "ownership-disabled"
        if not self._plugins.available:
            return self._plugins.reason or "unavailable"
        return ""

    @asynccontextmanager
    async def open(
        self, profile_headers: Mapping[str, str]
    ) -> AsyncIterator[CollaborationRepairServiceProtocol]:
        """Yield one repair service for this request; close its plugins after.

        ``profile_headers`` is the already-filtered authentication context the
        HTTP adapter submitted (Cookie / Authorization); the factory neither
        reads nor widens it.

        Raises ``RepairPreconditionError`` (``collaboration_sync_unavailable``)
        before yielding on an unavailable deployment; inside the block the
        service itself may raise ``RepairPreconditionError`` or
        ``RepairFactsError`` — the outcome vocabularies live in the Core module
        README (``core/bot_management/collaboration_repair``); the HTTP mapping
        belongs to the adapter.
        """
        reason = self._refusal_reason()
        if reason:
            logger.warning("[COLLABORATION-REPAIR] unavailable: reason=%s", reason)
            raise RepairPreconditionError(
                RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
            )

        metadata: BcsCollaborationMetadataPlugin | None = None
        ownership: BcsOwnershipPlugin | None = None
        managers: BcsManagersPlugin | None = None
        try:
            metadata = self._plugins.metadata(profile_headers)
            ownership = self._plugins.ownership()
            managers = self._plugins.managers()
            yield CollaborationRepairService(
                self._reader,
                self._ownership_reader,
                self._managers_reader,
                metadata,
                ownership,
                managers,
                timeout_seconds=self._timeout_seconds,
                clock=time.monotonic,
            )
        finally:
            # Reverse construction order; a partially built set is still
            # closed, and one failing close never strands the plugins after
            # it in this loop (or leaks a client into the next request's loop).
            # Purpose of swallowing here: do not let a post-report close failure
            # mask the repair outcome the caller already received.
            for plugin in (managers, ownership, metadata):
                if plugin is not None:
                    await _close_quietly(plugin)


class CollaborationRepairModule(Module):
    """Wire the default-off, request-scoped collaboration repair factory."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            CollaborationRepairReadRepository,
            to=SqlCollaborationRepairReadRepository,
            scope=singleton,
        )

    @singleton
    @provider
    def collaboration_repair_plugins(self) -> CollaborationRepairPlugins:
        """The production delivery source, or the reason it must not send.

        Nothing is resolved while the feature (or its fixed ownership
        component) is off: the factory refuses on config alone, so a
        disabled deployment never reads a secret or builds a transport.
        Otherwise the provider-scope check is the transport's own predicate
        (``_get_provider_config``: prod/pre always, dev only with the
        explicit registration opt-in, and never with empty credentials), so
        the factory refuses exactly where the transports would skip.
        """
        injector = self.__injector__
        config = injector.get(ManagerSyncConfig)
        # Same refusal vocabulary as the factory: a disabled deployment and a
        # managers-only deployment are distinct, diagnosable tokens.
        if not config.enabled:
            return HttpCollaborationRepairPlugins.unavailable("disabled")
        if not config.ownership_enabled:
            return HttpCollaborationRepairPlugins.unavailable("ownership-disabled")
        # Reuses the manager-sync composition's minter (same secret name, same
        # BCS envelope) instead of growing a second signing path.
        mint_principal = _build_principal_minter(
            injector.get(SecretResolver), injector.get(SecretNamesConfig)
        )
        if mint_principal is None:
            return HttpCollaborationRepairPlugins.unavailable(
                "principal-signing-key-required"
            )
        if _get_provider_config(get_current_env(), injector.get(BcnConfig)) is None:
            return HttpCollaborationRepairPlugins.unavailable(
                "provider-scope-unavailable"
            )
        return HttpCollaborationRepairPlugins(
            bcn_service=injector.get(BcnService),
            bcs_base_url=config.bcs_base_url,
            mint_principal=mint_principal,
        )

    @singleton
    @provider
    def collaboration_repair_factory(self) -> CollaborationRepairFactory:
        """The object an HTTP adapter injects; it never names an impl itself."""
        injector = self.__injector__
        plugins = injector.get(CollaborationRepairPlugins)
        # The request budget is the ``timeout`` passed to ``BcnService``
        # (stored as ``_timeout``, 30s today): repair adds no configuration of
        # its own. BcnService exposes no accessor for it, and Core is not
        # widened for a composition-only read.
        timeout_seconds = injector.get(BcnService)._timeout
        if plugins.available and timeout_seconds <= 0:
            # A non-positive transport timeout would make every item fail
            # before its first send; refuse to construct instead of pretending
            # the request has a budget.
            plugins = HttpCollaborationRepairPlugins.unavailable("invalid-budget")
        return CollaborationRepairFactory(
            plugins,
            injector.get(CollaborationRepairReadRepository),
            injector.get(OwnershipSyncReadRepository),
            injector.get(ManagerSyncReadRepository),
            injector.get(ManagerSyncConfig),
            timeout_seconds=timeout_seconds,
        )


__all__ = [
    "CollaborationRepairFactory",
    "CollaborationRepairModule",
    "CollaborationRepairPlugins",
    "HttpCollaborationRepairPlugins",
]
