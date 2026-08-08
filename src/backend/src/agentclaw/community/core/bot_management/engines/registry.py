"""Composition root for engine provisioning strategies."""
from __future__ import annotations

from typing import Any, Protocol

from .aicoding.strategy import (
    AICODING_ENGINE_TYPE,
    CLAUDE_CODE_ENGINE_TYPE,
    AicodingBaasEngineBucketResolver,
    AicodingProvisioningStrategy,
    CODING_TEMPLATE_TYPES,
)
from .default import DefaultProvisioningStrategy
from .provisioning import BotProvisioningContext, EngineProvisioningStrategy


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


def _build_default_baas_engine_bucket_resolver_registry() -> BaasEngineBucketResolverRegistry:
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


__all__ = [
    "AICODING_ENGINE_TYPE",
    "BaasEngineBucketResolver",
    "BaasEngineBucketResolverRegistry",
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "get_baas_engine_bucket_resolver_registry",
    "get_engine_provisioning_registry",
    "normalize_engine_type",
    "resolve_baas_engine_bucket",
    "resolve_provisioning",
]
