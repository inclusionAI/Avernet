"""DI wiring for the Backend→BCS manager sync (Task 6 composition root).

Bindings, all default-off until the ``bot_manager_sync`` block enables
the feature and everything the enabled posture requires is actually
resolvable:

- ``ManagerSyncConfig`` — the strict parse of the ``bot_manager_sync``
  block. Structural errors raise here and fail the boot (the container
  eagerly resolves this binding on every profile).
- ``ManagerSyncStack`` — the (un)available delivery + coordination
  plugin pair. The community column runs the prod stack (Redis-backed
  coordination on the shared cache's Redis URL, real HTTP delivery
  direct to the BCS origin with self-signed service principals minted
  from the shared signing key); the test column overrides it with the
  Recording / Memory doubles. An unavailable stack carries a stable
  reason word, never a raised exception and never a fabricated fallback
  (no process-local lock, no empty-string secrets).
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
unavailable stack, not a degraded lock. Delivery authentication resolves
through the same shared gateway-principal signing key every other
principal flow in this component uses
(``SecretNamesConfig.gateway_principal_signing_key`` via
:func:`resolve_signing_key`); a missing key makes the feature
unavailable with a diagnostic — never an empty-string or unsigned-key
fallback — and secret material is never logged or repr'ed.

``coordination_mode`` selects the coordinator implementation: ``redis``
(the default, production-only posture) builds the Redis coordinator
over the shared cache URL; ``memory`` — the **single-replica downgrade
form** — builds the in-process
:class:`MemoryManagerSyncCoordinator` with the configured generation,
exchanging cross-process coordination for a dev stack that has exactly
one Backend process (singlebox). Delivery stays the real
:class:`HttpBcsManagersPlugin` direct to BCS in BOTH modes, and the
signing-key availability check is identical — ``memory`` never loosens
it, it only swaps the coordination boundary.
"""
from __future__ import annotations

from collections.abc import Callable
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
from agentclaw.community.di.config import SecretNamesConfig
from agentclaw.community.di.modules.config_module import read_user_config
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.manager_sync_coordination import (
    ManagerSyncCoordinator,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.gateway_principal_config import (
    bcs_service_principal_signer_config,
    resolve_signing_key,
)

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


def build_memory_manager_sync_stack(
    config: ManagerSyncConfig, mint_principal: Callable[[str], str]
) -> "ManagerSyncStack":
    """The ``memory``-mode stack: real HTTP delivery + in-process coordination.

    The single-replica downgrade form (singlebox/dev only — see the
    config module docstring). Delivery stays the real self-signed HTTP
    plugin direct to the BCS origin, and coordination swaps to the
    in-process coordinator bound to the configured generation sentinel
    value. The coordinator ctor takes the generation only: the namespace
    identity (``target_id``) is part of the Redis key layout and has no
    in-process analogue.

    Fail-closed on data environments outside the dev family: a pre/prod
    deployment asking for in-process coordination is a structural
    configuration error (two sender processes would race undetected),
    so this raises — it is never silently accepted, never downgraded
    to a hidden Redis fallback.
    """
    from agentclaw.community.plugins.community.bcs_managers import (
        HttpBcsManagersPlugin,
    )
    from agentclaw.community.plugins.local.manager_sync_coordination import (
        MemoryManagerSyncCoordinator,
    )
    from agentclaw.community.utils.env_utils import get_current_env

    if get_current_env() != "dev":
        raise ValueError(
            "bot_manager_sync.coordination_mode=memory is the single-replica "
            "dev-stack downgrade (singlebox) and is not a valid "
            f"{get_current_env()!r} posture — keep the default redis mode "
            "with shared non-evicting Redis"
        )
    coordinator = MemoryManagerSyncCoordinator(generation=config.coordination_generation)
    return ManagerSyncStack.available_with(
        HttpBcsManagersPlugin(
            base_url=config.bcs_base_url, mint_principal=mint_principal
        ),
        coordinator,
    )


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
        secret_names: SecretNamesConfig,
        cache_config: community_cfg.CommunityCacheConfig,
    ) -> ManagerSyncStack:
        """The community/prod stack (the test column overrides this key).

        ``CommunityCacheConfig`` is injected, so only deployments that
        actually support the shared cache can ever construct through to
        the Redis-coordinator branch below; test profiles rebind this
        provider wholesale with the Recording/Memory doubles and never
        resolve this one, while the singlebox column overrides it with
        the memory-mode stack builder (real delivery + in-process
        coordination) so the dev stack never requires shared Redis.
        """
        if not config.enabled:
            return ManagerSyncStack.unavailable("disabled")
        memory_mode = config.coordination_mode == "memory"
        if memory_mode:
            # Single-replica downgrade (singlebox/dev stacks only): a
            # deployment authorizing memory mode opts out of shared Redis
            # entirely, so the shared-redis requirement does not apply —
            # one process, one writer, whole-stack restart. The
            # signing-key availability check is identical in both
            # coordination modes; ``memory`` never loosens it.
            mint_principal = _build_principal_minter(secret_resolver, secret_names)
            if mint_principal is None:
                return ManagerSyncStack.unavailable("principal-signing-key-required")
            logger.info(
                "[MANAGER-SYNC] enabled stack resolving (memory mode — "
                "single-replica dev downgrade, in-process coordination): "
                "target=%s env=%s bcs=%s generation=provided",
                config.target_id,
                config.env,
                config.bcs_base_url,
            )
            return build_memory_manager_sync_stack(config, mint_principal)
        # Shared Redis is mandatory in the default (production) mode:
        # markers must not be evicted and two replicas must coordinate.
        # No process-local fallback exists for it.
        redis_url = cache_config.redis_url
        if not redis_url:
            return ManagerSyncStack.unavailable("shared-redis-required")
        mint_principal = _build_principal_minter(secret_resolver, secret_names)
        if mint_principal is None:
            return ManagerSyncStack.unavailable("principal-signing-key-required")
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
            "bcs=%s shared-redis=yes generation=provided",
            config.target_id,
            config.env,
            config.bcs_base_url,
        )
        return ManagerSyncStack.available_with(
            HttpBcsManagersPlugin(
                base_url=config.bcs_base_url, mint_principal=mint_principal
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

        Reads the (singleton) protocol binding so the lifecycle drives the
        *same* dispatcher instance the business code submits to.
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
