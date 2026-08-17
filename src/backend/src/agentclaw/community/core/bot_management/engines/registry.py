"""Composition root for engine provisioning strategies."""

from __future__ import annotations

from typing import Any, Protocol, TYPE_CHECKING

from .aicoding.strategy import (
    AICODING_ENGINE_TYPE,
    CLAUDE_CODE_ENGINE_TYPE,
    AicodingBaasEngineBucketResolver,
    AicodingProvisioningStrategy,
    CODING_TEMPLATE_TYPES,
)
from .aicoding.mcp_defaults import AicodingMcpDefaultsResolver
from .default import DefaultProvisioningStrategy
from .provisioning import BotProvisioningContext, EngineProvisioningStrategy

from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    # Neutral contract only — never import core/devices here (the engine
    # composition root must not reverse-couple into device internals).
    from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = get_logger()


class BaasEngineBucketResolver(Protocol):
    """Resolver that may map an engine context to a BaaS engine bucket.

    Return ``None`` when the resolver does not own the input.  The routing
    registry continues with the next resolver and eventually falls back to the
    normalized engine type.
    """

    def resolve_baas_engine_bucket(
        self,
        *,
        normalized_engine_type: str,
        template_type: str | None,
    ) -> str | None:
        """Return a bucket override, or ``None`` when not applicable."""


class BaasEngineBucketResolverRegistry:
    """Registry for engine-contributed BaaS bucket resolvers."""

    def __init__(self) -> None:
        self._resolvers: list[BaasEngineBucketResolver] = []

    def register(self, resolver: BaasEngineBucketResolver) -> None:
        self._resolvers.append(resolver)

    def resolve(
        self,
        *,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        normalized_engine = normalize_engine_type(engine_type)
        for resolver in self._resolvers:
            engine_bucket = resolver.resolve_baas_engine_bucket(
                normalized_engine_type=normalized_engine,
                template_type=template_type,
            )
            if engine_bucket is not None:
                return engine_bucket
        return normalized_engine


class DefaultCapabilitiesEngineBucketResolver(Protocol):
    """Resolver that may map an engine context to a default-capabilities bucket."""

    def resolve_default_capabilities_engine_bucket(
        self,
        *,
        normalized_engine_type: str,
        template_type: str | None,
    ) -> str | None:
        """Return a bucket override, or ``None`` when not applicable."""


class DefaultCapabilitiesEngineBucketResolverRegistry:
    """Registry for engine-contributed default MCP/CLI bucket resolvers."""

    def __init__(self) -> None:
        self._resolvers: list[DefaultCapabilitiesEngineBucketResolver] = []

    def register(self, resolver: DefaultCapabilitiesEngineBucketResolver) -> None:
        self._resolvers.append(resolver)

    def resolve(
        self,
        *,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        normalized_engine = normalize_engine_type(engine_type)
        for resolver in self._resolvers:
            engine_bucket = resolver.resolve_default_capabilities_engine_bucket(
                normalized_engine_type=normalized_engine,
                template_type=template_type,
            )
            if engine_bucket is not None:
                return engine_bucket
        return normalized_engine


class McpDefaultsResolver(Protocol):
    """Bucket-specific hook for deriving effective default MCP configs."""

    def resolve(
        self,
        default_servers: list[dict],
        ext_info: dict | None = None,
    ) -> list[dict]:
        """Return effective default MCP configs for one engine bucket."""


class McpDefaultsResolverRegistry:
    """Registry for engine-contributed MCP default resolvers."""

    def __init__(self) -> None:
        self._resolvers: dict[str, McpDefaultsResolver] = {}

    def register(self, engine_bucket: str, resolver: McpDefaultsResolver) -> None:
        if engine_bucket in self._resolvers:
            raise ValueError(
                f"MCP defaults resolver already registered: {engine_bucket}"
            )
        self._resolvers[engine_bucket] = resolver

    def resolve(self, engine_bucket: str) -> McpDefaultsResolver | None:
        return self._resolvers.get(engine_bucket)


class EngineProvisioningRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, EngineProvisioningStrategy] = {}
        self._default = DefaultProvisioningStrategy()

    def register(self, strategy: EngineProvisioningStrategy) -> None:
        engine_type = strategy.engine_type
        if engine_type in self._strategies:
            # Guard against accidental double registration which would silently
            # overwrite a previously installed strategy.
            raise ValueError(
                f"engine provisioning strategy already registered: {engine_type}"
            )
        self._strategies[engine_type] = strategy

    def resolve(self, engine_type: str) -> EngineProvisioningStrategy:
        """Resolve the strategy for a known engine type.

        Unknown engine types fall back to the default no-op strategy.  Call
        sites that may not have ``active_engine`` at all (legacy / partial
        metadata) should use ``resolve_for_context`` instead of passing a
        sentinel here.
        """
        return self._strategies.get(engine_type, self._default)

    def resolve_bot_engine(self, bot: dict[str, Any]) -> str | None:
        """Resolve the effective runtime engine for a bot record."""
        active_engine = bot.get("active_engine")
        strategy = self.resolve(normalize_engine_type(active_engine, default=""))
        return strategy.resolve_bot_engine(bot)

    def resolve_for_context(
        self, ctx: BotProvisioningContext
    ) -> EngineProvisioningStrategy:
        """Resolve a strategy for legacy call sites with partial metadata.

        Prefer ``active_engine``.  If older call sites only pass
        ``template_type``, route known legacy coding templates to the coding
        strategy so historical TemplateService signatures keep working while
        keeping the rule in one place.  Template-factory templates should be
        detected from active_engine + template_config at the strategy layer; do
        not infer them here from template keys or engine_config lists.  An
        explicit (non-empty) engine always wins over a coding ``template_type``,
        so dirty data such as ``openclaw`` + ``personalCoding`` does not
        accidentally get AICoding provisioning.
        """
        if ctx.active_engine:
            return self.resolve(ctx.active_engine)
        if ctx.template_type in CODING_TEMPLATE_TYPES:
            return self.resolve(AICODING_ENGINE_TYPE)
        return self._default


def _build_default_registry() -> EngineProvisioningRegistry:
    """Assemble the process-wide strategy registry with all known engines.

    Pure and side-effect free: it only instantiates lightweight strategy
    objects.  Built eagerly at import so the single-instance contract is
    obvious and no lazy double-checked-locking is needed.
    """
    registry = EngineProvisioningRegistry()
    registry.register(AicodingProvisioningStrategy(AICODING_ENGINE_TYPE))
    registry.register(AicodingProvisioningStrategy(CLAUDE_CODE_ENGINE_TYPE))
    for engine_type in ("openclaw", "teclaw", "hermes"):
        registry.register(DefaultProvisioningStrategy(engine_type))
    return registry


# Eager module-level singleton: built exactly once at import.  The registry is
# bootstrap state (a fixed set of strategies), so there is no need for lazy DCL.
_REGISTRY: EngineProvisioningRegistry = _build_default_registry()


def get_engine_provisioning_registry() -> EngineProvisioningRegistry:
    """Return the process-wide strategy registry."""
    return _REGISTRY


def resolve_bot_engine(bot: dict[str, Any]) -> str | None:
    """Resolve the effective runtime engine for a bot record."""
    return get_engine_provisioning_registry().resolve_bot_engine(bot)


def _build_default_baas_engine_bucket_resolver_registry() -> (
    BaasEngineBucketResolverRegistry
):
    """Assemble the process-wide BaaS bucket resolver registry."""
    registry = BaasEngineBucketResolverRegistry()
    registry.register(AicodingBaasEngineBucketResolver())
    return registry


_BAAS_ENGINE_BUCKET_RESOLVER_REGISTRY: BaasEngineBucketResolverRegistry = (
    _build_default_baas_engine_bucket_resolver_registry()
)


def get_baas_engine_bucket_resolver_registry() -> BaasEngineBucketResolverRegistry:
    """Return the process-wide BaaS bucket resolver registry."""
    return _BAAS_ENGINE_BUCKET_RESOLVER_REGISTRY


def normalize_engine_type(engine_type: str | None, *, default: str = "openclaw") -> str:
    """Normalize public engine spelling to the registry key form."""
    return (engine_type or default).strip().lower().replace("-", "_")


def resolve_baas_engine_bucket(
    *,
    engine_type: str | None,
    template_type: str | None,
) -> str:
    """Resolve the engine bucket used by BaaS template/rollout routing.

    This public entrypoint delegates to the bucket resolver registry. Concrete
    engine-specific overrides are contributed by registered resolvers; unclaimed
    inputs fall back to the normalized engine type.
    """
    return get_baas_engine_bucket_resolver_registry().resolve(
        engine_type=engine_type,
        template_type=template_type,
    )


def _build_default_capabilities_engine_bucket_resolver_registry() -> (
    DefaultCapabilitiesEngineBucketResolverRegistry
):
    """Assemble the process-wide default MCP/CLI bucket resolver registry."""
    registry = DefaultCapabilitiesEngineBucketResolverRegistry()
    registry.register(AicodingBaasEngineBucketResolver())
    return registry


_DEFAULT_CAPABILITIES_ENGINE_BUCKET_RESOLVER_REGISTRY = (
    _build_default_capabilities_engine_bucket_resolver_registry()
)


def get_default_capabilities_engine_bucket_resolver_registry() -> (
    DefaultCapabilitiesEngineBucketResolverRegistry
):
    """Return the process-wide default-capabilities bucket resolver registry."""
    return _DEFAULT_CAPABILITIES_ENGINE_BUCKET_RESOLVER_REGISTRY


def resolve_default_capabilities_engine_bucket(
    *,
    engine_type: str | None,
    template_type: str | None,
) -> str:
    """Resolve the engine bucket used by default MCP/CLI capability routing."""
    return get_default_capabilities_engine_bucket_resolver_registry().resolve(
        engine_type=engine_type,
        template_type=template_type,
    )


def _build_mcp_defaults_resolver_registry() -> McpDefaultsResolverRegistry:
    """Assemble the process-wide MCP defaults resolver registry."""
    registry = McpDefaultsResolverRegistry()
    registry.register(AICODING_ENGINE_TYPE, AicodingMcpDefaultsResolver())
    return registry


_MCP_DEFAULTS_RESOLVER_REGISTRY = _build_mcp_defaults_resolver_registry()


def get_mcp_defaults_resolver_registry() -> McpDefaultsResolverRegistry:
    """Return the process-wide MCP defaults resolver registry."""
    return _MCP_DEFAULTS_RESOLVER_REGISTRY


def resolve_provisioning(
    *,
    bot_id: str,
    owner_id: str,
    bot_type: str,
    active_engine: str | None = None,
    template_type: str | None = None,
    template_config: dict[str, Any] | None = None,
) -> tuple[BotProvisioningContext, EngineProvisioningStrategy]:
    """Build the provisioning context and resolve the engine strategy.

    Single entry point so ``BotProvisioningContext`` construction + strategy
    resolution live in one place.  Every service call site (BotService,
    BaasDeviceService, TemplateService, utils) goes through here instead of
    repeating ``BotProvisioningContext(...)`` + ``resolve_for_context(...)``.

    Returns the built context together with the resolved strategy so the caller
    can invoke the specific hook it needs (``build_extra_envs`` /
    ``extract_runtime_token`` / ``should_encrypt_template_token``).
    """
    ctx = BotProvisioningContext(
        bot_id=bot_id,
        owner_id=owner_id,
        bot_type=bot_type,
        active_engine=active_engine,
        template_type=template_type,
        template_config=template_config,
    )
    strategy = get_engine_provisioning_registry().resolve_for_context(ctx)
    return ctx, strategy


def resolve_outbound_rule_envelope(
    *,
    bot_id: str,
    owner_id: str,
    bot_query: Any,
    template_service: Any | None,
    secret_resolver: "SecretResolver | None",
    theta_master_key_secret: str = "",
) -> dict[str, Any] | None:
    """Resolve the engine-owned outbound-rule envelope for a bot at bootstrap.

    Mirrors the create-chain provisioning resolution (``resolve_provisioning``
    -> ``EngineProvisioningStrategy.build_extra_properties``) so that bootstrap
    rebuilding an outbound rule preserves a Bot's custom egress-key instead of
    falling back to the deployment default credential. Engine-specific knowledge
    (e.g. theta-key decryption) still lives only in each engine strategy; this
    function is engine-agnostic orchestration: fetch bot -> fetch template_config
    -> dispatch to the resolved strategy -> return the generic envelope
    (``{"outbound_api_key": ...}``). Returns ``None`` (legacy default-credential
    fallback, zero regression) when there is no custom key, a dependency is
    missing, or resolution fails.

    ``bot_query`` duck-types ``BotQueryProtocol.get_by_id_and_owner`` and
    ``template_service`` duck-types ``TemplateConfigReader.get_template_config``;
    typed as ``Any`` so the neutral composition root does not reverse-import
    ``core/devices`` (see architecture review).
    """
    logger.info(
        "[engines.resolve_outbound_rule_envelope] start: "
        "bot_id=%s, owner_id=%s, has_template_service=%s, "
        "has_secret_resolver=%s, has_theta_master_key_secret=%s",
        bot_id,
        owner_id,
        template_service is not None,
        secret_resolver is not None,
        bool(theta_master_key_secret),
    )
    if template_service is None:
        logger.warning(
            "[engines.resolve_outbound_rule_envelope] fallback: "
            "bot_id=%s, owner_id=%s, reason=template_service_missing",
            bot_id,
            owner_id,
        )
        return None

    bot = bot_query.get_by_id_and_owner(bot_id, owner_id)
    if not bot:
        logger.warning(
            "[engines.resolve_outbound_rule_envelope] fallback: "
            "bot_id=%s, owner_id=%s, reason=bot_not_found",
            bot_id,
            owner_id,
        )
        return None

    active_engine = bot.get("active_engine")
    template_type = bot.get("template_type")
    try:
        template_config = template_service.get_template_config(bot_id)
    except Exception as e:
        logger.warning(
            "[engines.resolve_outbound_rule_envelope] get_template_config "
            "failed: bot_id=%s, owner_id=%s, error=%s",
            bot_id,
            owner_id,
            e,
        )
        return None

    logger.info(
        "[engines.resolve_outbound_rule_envelope] context: "
        "bot_id=%s, owner_id=%s, active_engine=%s, template_type=%s, "
        "has_template_config=%s",
        bot_id,
        owner_id,
        active_engine,
        template_type,
        isinstance(template_config, dict),
    )

    try:
        ctx, strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=bot.get("bot_type", ""),
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        extra_properties = strategy.build_extra_properties(
            ctx,
            secret_resolver=secret_resolver,
            theta_master_key_secret=theta_master_key_secret,
        )
        custom_outbound_key_resolved = bool(
            isinstance(extra_properties, dict)
            and extra_properties.get("outbound_api_key")
        )
        logger.info(
            "[engines.resolve_outbound_rule_envelope] result: "
            "bot_id=%s, owner_id=%s, strategy=%s, "
            "custom_outbound_key_resolved=%s",
            bot_id,
            owner_id,
            type(strategy).__name__,
            custom_outbound_key_resolved,
        )
        if not custom_outbound_key_resolved:
            logger.warning(
                "[engines.resolve_outbound_rule_envelope] fallback: "
                "bot_id=%s, owner_id=%s, active_engine=%s, "
                "template_type=%s, reason=strategy_returned_no_custom_key, "
                "has_secret_resolver=%s, has_theta_master_key_secret=%s",
                bot_id,
                owner_id,
                active_engine,
                template_type,
                secret_resolver is not None,
                bool(theta_master_key_secret),
            )
        return extra_properties
    except Exception as e:  # pragma: no cover - defensive: resolve_provisioning
        # and each registered strategy's build_extra_properties never raise for
        # any real bot (they fail-open internally); this only fires on a future
        # broken engine/strategy or registry corruption. Fail-safe to None.
        logger.warning(
            "[engines.resolve_outbound_rule_envelope] resolve failed: "
            "bot_id=%s, error=%s",
            bot_id,
            e,
        )
        return None


__all__ = [
    "AICODING_ENGINE_TYPE",
    "BaasEngineBucketResolver",
    "BaasEngineBucketResolverRegistry",
    "BotProvisioningContext",
    "DefaultCapabilitiesEngineBucketResolver",
    "DefaultCapabilitiesEngineBucketResolverRegistry",
    "EngineProvisioningRegistry",
    "get_baas_engine_bucket_resolver_registry",
    "get_default_capabilities_engine_bucket_resolver_registry",
    "get_mcp_defaults_resolver_registry",
    "get_engine_provisioning_registry",
    "normalize_engine_type",
    "resolve_baas_engine_bucket",
    "resolve_bot_engine",
    "resolve_default_capabilities_engine_bucket",
    "resolve_provisioning",
    "resolve_outbound_rule_envelope",
]
