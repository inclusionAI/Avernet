"""DI wiring for the Backend→BCS manager sync (Task 6 composition root).

Bindings, all default-off until the ``bot_manager_sync`` block enables
the feature and everything the enabled posture requires is actually
resolvable:

- ``ManagerSyncConfig`` — the strict parse of the ``bot_manager_sync``
  block. Structural errors raise here and fail the boot (the container
  eagerly resolves this binding on every profile).
- ``ManagerSyncStack`` — the (un)available delivery + coordination
  plugin pair. The community column runs the prod stack (Redis-backed
  coordination on the shared cache's Redis URL, trusted-App HTTP delivery
  with per-tenant credentials from the SecretResolver); the test column
  overrides it with the Recording / Memory doubles. An unavailable stack
  carries a stable reason word, never a raised exception and never a
  fabricated fallback (no process-local lock, no empty-string secrets).
- ``BotManagerSyncProtocol`` — :class:`DisabledBotManagerSync` (zero
  resources) when the feature is off or the stack is unavailable; the
  bounded :class:`BotManagerSyncDispatcher` when everything resolved.
- ``BotManagerSyncLifecycle`` — the loop-owning participant, or its
  zero-resource disabled twin, so lifecycle discovery never sees a raise.
- ``ManagerSyncRepairService`` — the controlled maintenance surface
  (first backfill / targeted compensation / marker recovery). Never routed
  over HTTP; the deployment's existing maintenance environment resolves
  it from the injector. Off/unavailable postures bind the zero-resource
  :class:`DisabledManagerSyncRepair` twin, never a fabricated CAS answer.

The Redis URL never appears in this module: it comes from the existing
``CommunityCacheConfig``, and a deployment without shared Redis gets an
unavailable stack, not a degraded lock. Credentials resolve through
``SecretResolver.get_secret(...).secret_value``; a missing one makes
the feature unavailable with a diagnostic — never an empty-string fake
credential — and secret material is never logged or repr'ed.
"""
from __future__ import annotations

from dataclasses import dataclass

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DisabledBotManagerSync,
)
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle,
    DisabledBotManagerSyncLifecycle,
)
from agentclaw.community.core.bot_collaborator.manager_sync.repair import (
    DisabledManagerSyncRepair,
    ManagerSyncRepairService,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.repository.implementations.bot.manager_sync import (
    SqlManagerSyncReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.di import config_community as community_cfg
from agentclaw.community.di.bot_manager_sync_config import (
    CONFIG_BLOCK_NAME,
    ManagerSyncConfig,
    parse_manager_sync_config,
)
from agentclaw.community.di.modules.config_module import read_user_config
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.manager_sync_coordination import (
    ManagerSyncCoordinator,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = get_logger()

#: Bounded connect timeout (seconds) for the coordination Redis client.
#: Small relative to the 15s round budget — claim/renew/begin/finish must
#: each fit inside one round — so a stalled Redis connect fails fast
#: instead of parking every sync worker indefinitely.
REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS = 3.0

#: Bounded read/write timeout (seconds) for one coordination command —
#: same round-budget rationale as the connect timeout above.
REDIS_SOCKET_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ManagerSyncStack:
    """The plugin pair an enabled syncer runs on — or why it cannot.

    ``available`` False means the feature must bind disabled for this
    deployment; ``reason`` is a stable diagnostic word/phrase (never
    secret material). ``bcs`` / ``coordinator`` are ``None`` exactly when
    unavailable — the composition root checks ``available`` first and
    never dereferences them in that posture.
    """

    bcs: BcsManagersPlugin | None
    coordinator: ManagerSyncCoordinator | None
    available: bool
    reason: str = ""

    @classmethod
    def available_with(
        cls, bcs: BcsManagersPlugin, coordinator: ManagerSyncCoordinator
    ) -> "ManagerSyncStack":
        return cls(bcs=bcs, coordinator=coordinator, available=True)

    @classmethod
    def unavailable(cls, reason: str) -> "ManagerSyncStack":
        return cls(bcs=None, coordinator=None, available=False, reason=reason)


def _resolve_app_credentials(
    config: ManagerSyncConfig, secret_resolver: SecretResolver
) -> tuple[dict[str, str], str]:
    """Resolve every configured tenant's trusted-App credential.

    Returns ``(credentials, missing_reason)`` — ``missing_reason`` is
    empty when every lookup succeeded. The resolver is asked by *name*
    (the map only selects which named secret each tenant uses); a
    missing, empty, or unreadable secret makes the whole feature
    unavailable rather than forging an empty-string credential. Secret
    values never travel into logs, diagnostics, or exceptions.
    """
    credentials: dict[str, str] = {}
    missing: list[str] = []
    for tenant, secret_name in config.app_secret_names_by_tenant.items():
        secret = None
        value: str | None = None
        try:
            secret = secret_resolver.get_secret(secret_name)
        except Exception as exc:  # noqa: BLE001 — unavailable, not a boot crash
            logger.error(
                "[MANAGER-SYNC] credential_lookup_failed secret_name=%s error=%s",
                secret_name,
                type(exc).__name__,
            )
        if secret is not None:
            value = getattr(secret, "secret_value", None)
        if not isinstance(value, str) or not value:
            missing.append(secret_name)
            continue
        credentials[tenant] = value
    if missing:
        reason = "missing-credential:" + ",".join(sorted(missing)[:8])
    else:
        reason = ""
    return credentials, reason


class BotManagerSyncModule(Module):
    """Composition root for the manager sync feature (default off)."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            ManagerSyncReadRepository,
            to=SqlManagerSyncReadRepository,
            scope=singleton,
        )

    @singleton
    @provider
    def manager_sync_config(self) -> ManagerSyncConfig:
        """Strict parse of the ``bot_manager_sync`` block (see module)."""
        block = read_user_config().get(CONFIG_BLOCK_NAME)
        return parse_manager_sync_config(block)

    @singleton
    @provider
    @inject
    def manager_sync_stack(
        self,
        config: ManagerSyncConfig,
        secret_resolver: SecretResolver,
        cache_config: community_cfg.CommunityCacheConfig,
    ) -> ManagerSyncStack:
        """The community/prod stack (the test column overrides this key).

        ``CommunityCacheConfig`` is injected, so only deployments that
        actually support the shared cache can ever construct the Redis
        coordinator; test profiles rebind this provider wholesale with
        the Recording/Memory doubles and never resolve this one.
        """
        if not config.enabled:
            return ManagerSyncStack.unavailable("disabled")
        # Shared Redis is mandatory: markers must not be evicted and two
        # replicas must coordinate. No process-local fallback exists.
        redis_url = cache_config.redis_url
        if not redis_url:
            return ManagerSyncStack.unavailable("shared-redis-required")
        credentials, missing_reason = _resolve_app_credentials(
            config, secret_resolver
        )
        if missing_reason:
            return ManagerSyncStack.unavailable(missing_reason)
        try:
            import redis  # community dep; import stays behind the gate

            client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — unavailable, not a crash
            logger.error(
                "[MANAGER-SYNC] redis_client_build_failed error=%s",
                type(exc).__name__,
            )
            return ManagerSyncStack.unavailable("redis-client-unbuildable")
        from agentclaw.community.plugins.community.bcs_managers import (
            HttpBcsManagersPlugin,
        )
        from agentclaw.community.plugins.community.manager_sync_coordination import (
            RedisManagerSyncCoordinator,
        )

        logger.info(
            "[MANAGER-SYNC] enabled stack resolving: target=%s env=%s "
            "tenants=%d shared-redis=yes generation=provided",
            config.target_id,
            config.env,
            len(credentials),
        )
        return ManagerSyncStack.available_with(
            HttpBcsManagersPlugin(
                base_url=config.gateway_base_url, app_credentials=credentials
            ),
            RedisManagerSyncCoordinator(
                target_id=config.target_id,
                generation=config.coordination_generation,
                client=client,
            ),
        )

    @singleton
    @provider
    def manager_sync(self) -> BotManagerSyncProtocol:
        """The business-facing entry point: disabled, or the dispatcher.

        Disabled is the zero-resource posture; enabled builds the service
        + dispatcher over the resolved stack and gates admissions to the
        deployment env from configuration. Dependencies resolve lazily
        (after the enabled check) so a disabled deployment never needs a
        shared Redis binding or a secret backend at all.
        """
        config = self.__injector__.get(ManagerSyncConfig)
        if not config.enabled:
            return DisabledBotManagerSync()
        stack = self.__injector__.get(ManagerSyncStack)
        if not stack.available or stack.bcs is None or stack.coordinator is None:
            logger.error(
                "[MANAGER-SYNC] syncer unavailable: reason=%s", stack.reason
            )
            return DisabledBotManagerSync()
        reader = self.__injector__.get(ManagerSyncReadRepository)
        service = BotManagerSyncService(
            reader=reader, bcs=stack.bcs, coordinator=stack.coordinator
        )
        return BotManagerSyncDispatcher(service, reader, env=config.env)

    @singleton
    @provider
    def manager_sync_lifecycle(self) -> BotManagerSyncLifecycle:
        """Lifecycle participant — the real loop owner, or its no-op twin.

        Reads the (singleton) protocol binding so the lifecycle drives
        the *same* dispatcher instance the business code submits to.
        """
        entry = self.__injector__.get(BotManagerSyncProtocol)
        if not isinstance(entry, BotManagerSyncDispatcher):
            return DisabledBotManagerSyncLifecycle(reason="disabled")
        stack = self.__injector__.get(ManagerSyncStack)
        if not stack.available or stack.bcs is None:
            return DisabledBotManagerSyncLifecycle(reason="disabled")
        return BotManagerSyncLifecycle(dispatcher=entry, bcs=stack.bcs)

    @singleton
    @provider
    def manager_sync_repair(self) -> ManagerSyncRepairService:
        """Controlled maintenance surface (first backfill / targeted
        compensation / marker recovery) — resolved from the injector by
        the deployment's existing maintenance environment only; no
        public HTTP route is ever wired to it.

        The default posture follows the protocol binding above: feature
        off or stack unavailable binds the zero-resource
        :class:`DisabledManagerSyncRepair` twin — there is no coordinator
        to CAS against in that posture, and no marker verdict may be
        fabricated. Dependencies resolve lazily (after the enabled
        check), so a disabled deployment never needs shared Redis or a
        secret backend to ask for the repair surface either.
        """
        config = self.__injector__.get(ManagerSyncConfig)
        if not config.enabled:
            return DisabledManagerSyncRepair(reason="disabled")
        stack = self.__injector__.get(ManagerSyncStack)
        if not stack.available or stack.coordinator is None:
            return DisabledManagerSyncRepair(
                reason=stack.reason or "unavailable"
            )
        reader = self.__injector__.get(ManagerSyncReadRepository)
        notifier = self.__injector__.get(BotManagerSyncProtocol)
        return ManagerSyncRepairService(
            reader=reader,
            notifier=notifier,
            coordinator=stack.coordinator,
        )


__all__ = ["BotManagerSyncModule", "ManagerSyncStack"]