"""Composition root for engine provisioning strategies."""
from __future__ import annotations

from .aicoding.strategy import AicodingProvisioningStrategy, CODING_TEMPLATE_TYPES
from .default import DefaultProvisioningStrategy
from .provisioning import BotProvisioningContext, EngineProvisioningStrategy


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
        ``template_type``, route known coding templates to the coding strategy
        so historical TemplateService signatures keep working while keeping the
        rule in one place.  An explicit (non-empty) engine always wins over a
        coding ``template_type``, so dirty data such as ``openclaw`` +
        ``personalCoding`` does not accidentally get AICoding provisioning.
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
    for engine_type in ("openclaw", "teclaw", "hermes"):
        registry.register(DefaultProvisioningStrategy(engine_type))
    return registry


# Eager module-level singleton: built exactly once at import.  The registry is
# bootstrap state (a fixed set of strategies), so there is no need for lazy DCL.
_REGISTRY: EngineProvisioningRegistry = _build_default_registry()


def get_engine_provisioning_registry() -> EngineProvisioningRegistry:
    """Return the process-wide strategy registry."""
    return _REGISTRY


__all__ = [
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "get_engine_provisioning_registry",
]
