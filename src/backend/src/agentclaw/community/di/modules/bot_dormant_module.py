"""BotDormantModule — production singletons for the dormant-bot subsystem.

Bindings registered here:
  - BaasDormantClient   — HTTP client to BaaS health-checker (env-driven base_url)
  - DormantBotService   — scan-and-decision orchestrator
  - ActivateBotService  — reactivates RECYCLED bots
  - WhitelistService    — batch-adds bots to the dormant whitelist
  - DormantBotLifecycle — single-cron lifecycle participant (auto-discovered by
                          discover_lifecycle_participants via LifecycleBase)

DormantBotService.__init__ takes ``bot_service: BotServiceProtocol`` as a
forward reference (TYPE_CHECKING guard) that injector cannot resolve by type
annotation alone.  We use a ``@provider`` factory to pass the already-bound
BotServiceProtocol explicitly.
"""
from __future__ import annotations

from typing import Any

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_service import BotServiceProtocol as _ApiBotServiceProtocol
from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService
from agentclaw.community.core.bot_dormant.baas_client import BaasDormantClient
from agentclaw.community.core.bot_dormant.internal_service import DormantInternalService
from agentclaw.community.core.bot_dormant.lifecycle import DormantBotLifecycle
from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.core.bot_dormant.protocols import (
    BotServiceProtocol as _DormantBotServiceProtocol,
)
from agentclaw.community.core.bot_dormant.scan_policy import DormantScanPolicyService
from agentclaw.community.core.bot_dormant.service import DormantBotService
from agentclaw.community.core.bot_dormant.whitelist_service import WhitelistService
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError as BotManagementNotFoundError,
)
from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.di.config import (
    DormantConfig,
    DormantInternalToken,
    DormantNotifyConfig,
    SecretNamesConfig,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


logger = get_logger()

# The Mist secret name backing the internal Bearer token is deployment config
# (``SecretNamesConfig.dormant_internal_token``, from the ``secret_names`` yaml
# block) — corp env overlays set the real name; the neutral shipped code carries
# no secret-registry reference (OSS-0 #3). An empty name (community / singlebox /
# test) short-circuits to the local fallback token below.

# Fallback token used when SecretResolver returns None (singlebox / CI /
# Mist unreachable). Lets local联调 still call /api/internal/dormant/*
# without provisioning a real Mist secret. Matches the pattern in
# core/skill_center/services/skill_scan.py (fallback_secret_value).
# NOTE: this is intentionally a publicly-visible string — it only ever
# gates singlebox/local where no real authority decision is at stake.
_SINGLEBOX_FALLBACK_TOKEN = "singlebox-dormant-token-local"


class _DormantBotServiceAdapter:
    """Normalize BotService behavior to the dormant module's local contract."""

    def __init__(self, bot_service: _ApiBotServiceProtocol) -> None:
        self._bot_service = bot_service

    def get_bot(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._bot_service.get_bot(*args, **kwargs)
        except BotManagementNotFoundError:
            return None

    def update_status(self, *args: Any, **kwargs: Any) -> Any:
        return self._bot_service.update_status(*args, **kwargs)

    def stop_bot(self, *args: Any, **kwargs: Any) -> Any:
        return self._bot_service.stop_bot(*args, **kwargs)

    def start_bot(self, *args: Any, **kwargs: Any) -> Any:
        return self._bot_service.start_bot(*args, **kwargs)


class BotDormantModule(Module):
    """Production bindings for the dormant-bot subsystem."""

    def configure(self, binder: Binder) -> None:
        # Simple singletons with @inject constructors — injector resolves all deps.
        # BaasDormantClient now takes Annotated[HttpClient, QUALIFIER_BAAS] via
        # @inject (bound by InfrastructureModule), so injector wires base_url
        # transitively via the qualified HttpClient — no explicit provider needed.
        binder.bind(BaasDormantClient, to=BaasDormantClient, scope=singleton)
        binder.bind(WhitelistService, to=WhitelistService, scope=singleton)
        binder.bind(DormantInternalService, to=DormantInternalService, scope=singleton)
        binder.bind(DormantOpsService, to=DormantOpsService, scope=singleton)
        binder.bind(DormantScanPolicyService, to=DormantScanPolicyService, scope=singleton)

    @singleton
    @provider
    @inject
    def _activate_bot_service(
        self,
        bot_service: _DormantBotServiceProtocol,
        passport_plugin: PassportPlugin,
    ) -> ActivateBotService:
        """Construct ActivateBotService with the passport plugin explicitly wired."""
        return ActivateBotService(
            bot_service=bot_service,
            passport_plugin=passport_plugin,
        )

    @singleton
    @provider
    @inject
    def _bridge_bot_service_protocol(
        self,
        bot_service: _ApiBotServiceProtocol,
    ) -> _DormantBotServiceProtocol:
        """Adapt the API service to the dormant module's local contract."""
        return _DormantBotServiceAdapter(bot_service)

    @singleton
    @provider
    @inject
    def _dormant_bot_service(
        self,
        db: DatabasePlugin,
        baas_client: BaasDormantClient,
        bot_service: _DormantBotServiceProtocol,
        passport_plugin: PassportPlugin,
        scan_policy: DormantScanPolicyService,
        common_whitelist_service: CommonWhiteListService,
        config: DormantConfig,
        notify_config: DormantNotifyConfig,
    ) -> DormantBotService:
        """Construct DormantBotService with explicit BotServiceProtocol injection.

        Inject DormantScanPolicyService so is_dry_run() reads ac_common_config
        at runtime instead of the legacy YAML/env knob.
        """
        return DormantBotService(
            db=db,
            baas_client=baas_client,
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            scan_policy=scan_policy,
            common_whitelist_service=common_whitelist_service,
            dry_run=config.dry_run,
            action_link_pattern=notify_config.action_link_pattern,
        )

    @singleton
    @provider
    @inject
    def _resolved_dormant_token(
        self,
        secret_resolver: SecretResolver,
        secret_names: SecretNamesConfig,
    ) -> DormantInternalToken:
        """Resolve the internal Bearer token via SecretResolver.

        The secret name comes from ``SecretNamesConfig.dormant_internal_token``
        (the ``secret_names`` yaml block; Mist via layotto in prod / pre).

        Resolution rules:
          - name is empty (community / singlebox / test — no corp secret name)
              → ``.value = _SINGLEBOX_FALLBACK_TOKEN`` (local fallback, so本地
                联调可调接口; unchanged from the previous None-resolver path)
          - Mist returns a secret with non-empty ``secret_value``
              → ``.value = secret.secret_value`` (prod / pre normal path)
          - Mist returns ``None`` or empty ``secret_value``
              → ``.value = _SINGLEBOX_FALLBACK_TOKEN``
          - resolver raises (transient Mist outage / network)
              → ``.value = ""`` (failure-closed: 401 all requests,
                 don't authorize garbage)
        """
        secret_name = secret_names.dormant_internal_token
        if not secret_name:
            logger.info(
                "[bot_dormant_module] no dormant token secret name configured — "
                "local fallback token in use"
            )
            return DormantInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        try:
            secret = secret_resolver.get_secret(secret_name=secret_name)
        except Exception:
            logger.exception(
                "[bot_dormant_module] SecretResolver.get_secret failed for %r — "
                "returning empty token (failure-closed)",
                secret_name,
            )
            return DormantInternalToken(value="")

        if secret is None:
            logger.info(
                "[bot_dormant_module] SecretResolver returned None for %r — "
                "singlebox/local fallback token in use",
                secret_name,
            )
            return DormantInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        value = getattr(secret, "secret_value", None)
        if not value:
            logger.warning(
                "[bot_dormant_module] secret %r resolved but secret_value "
                "empty — falling back to singlebox token",
                secret_name,
            )
            return DormantInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        return DormantInternalToken(value=str(value))

    @singleton
    @provider
    @inject
    def _dormant_bot_lifecycle(
        self,
        service: DormantBotService,
        cache: CachePlugin,
        scan_policy: DormantScanPolicyService,
    ) -> DormantBotLifecycle:
        """Construct DormantBotLifecycle — picked up by discover_lifecycle_participants."""
        return DormantBotLifecycle(
            service=service,
            cache=cache,
            scan_policy=scan_policy,
        )
