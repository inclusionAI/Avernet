"""Composition root for engine provisioning strategies."""

from __future__ import annotations

from typing import Any

from agentclaw.community.log import get_logger

from .aicoding.strategy import (
    AicodingProvisioningStrategy,
    CODING_TEMPLATE_TYPES,
    ThetaKeyExtraPropertiesContributor,
)
from .default import DefaultProvisioningStrategy
from .provisioning import (
    EngineExtraProperties,
    ExtraPropertiesContributor,
    BotProvisioningContext,
    EngineProvisioningStrategy,
)

logger = get_logger()


class EngineProvisioningRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, EngineProvisioningStrategy] = {}
        self._extra_properties_contributors: list[ExtraPropertiesContributor] = []
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

    def register_extra_properties_contributor(
        self, contributor: ExtraPropertiesContributor
    ) -> None:
        self._extra_properties_contributors.append(contributor)

    def build_extra_properties(
        self, ctx: BotProvisioningContext
    ) -> EngineExtraProperties | None:
        """Merge engine and independent extension contributions.

        The registry is the composition seam: generic callers pass one context,
        while each extension independently decides whether it applies.
        """
        merged: dict[str, Any] = {}
        strategy_properties = self.resolve_for_context(ctx).build_extra_properties(ctx)
        if strategy_properties is not None:
            merged.update(strategy_properties.to_dict())
        for contributor in self._extra_properties_contributors:
            properties = contributor.contribute(ctx)
            if properties is not None:
                merged.update(properties.to_dict())
        return EngineExtraProperties(merged) if merged else None

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
            return self.resolve("aicoding")
        return self._default


def _build_default_registry() -> EngineProvisioningRegistry:
    """Assemble the process-wide strategy registry with all known engines.

    Pure and side-effect free: it only instantiates lightweight strategy
    objects.  Built eagerly at import so the single-instance contract is
    obvious and no lazy double-checked-locking is needed.
    """
    registry = EngineProvisioningRegistry()
    registry.register(AicodingProvisioningStrategy("aicoding"))
    registry.register(AicodingProvisioningStrategy("claude_code"))
    registry.register_extra_properties_contributor(
        ThetaKeyExtraPropertiesContributor()
    )
    for engine_type in ("openclaw", "teclaw", "hermes"):
        registry.register(DefaultProvisioningStrategy(engine_type))
    return registry


# Eager module-level singleton: built exactly once at import.  The registry is
# bootstrap state (a fixed set of strategies), so there is no need for lazy DCL.
_REGISTRY: EngineProvisioningRegistry = _build_default_registry()


def get_engine_provisioning_registry() -> EngineProvisioningRegistry:
    """Return the process-wide strategy registry."""
    return _REGISTRY


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


def build_extra_properties_fail_open(
    *,
    bot_id: str,
    owner_id: str,
    bot_type: str,
    active_engine: str | None = None,
    template_type: str | None = None,
    template_config: dict[str, Any] | None = None,
    log_context: str = "engine_provisioning",
) -> EngineExtraProperties | None:
    """Build opaque provisioning properties without blocking generic provisioning.

    Strategy resolution and execution are extension hooks. Any failure falls
    back to ``None`` so generic lifecycle services preserve their legacy path.
    """
    try:
        ctx, _ = resolve_provisioning(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=bot_type,
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        return get_engine_provisioning_registry().build_extra_properties(ctx)
    except Exception as exc:
        logger.warning(
            "[%s] Provisioning extra properties fallback=none bot_id=%s error_type=%s",
            log_context,
            bot_id,
            type(exc).__name__,
        )
        return None


# Compatibility alias for callers outside this repository. Generic lifecycle
# code uses the engine-agnostic name above.
build_engine_extra_properties_fail_open = build_extra_properties_fail_open


__all__ = [
    "EngineExtraProperties",
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "build_extra_properties_fail_open",
    "build_engine_extra_properties_fail_open",
    "get_engine_provisioning_registry",
    "resolve_provisioning",
]
