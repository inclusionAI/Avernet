"""Composition root for engine provisioning strategies."""
from __future__ import annotations

from functools import lru_cache

from .aicoding.strategy import AicodingProvisioningStrategy, CODING_TEMPLATE_TYPES
from .default import DefaultProvisioningStrategy
from .provisioning import BotProvisioningContext, EngineProvisioningStrategy


class EngineProvisioningRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, EngineProvisioningStrategy] = {}
        self._default = DefaultProvisioningStrategy()

    def register(self, strategy: EngineProvisioningStrategy) -> None:
        self._strategies[strategy.engine_type] = strategy

    def resolve(self, engine_type: str | None) -> EngineProvisioningStrategy:
        if not engine_type:
            return self._default
        return self._strategies.get(engine_type, self._default)

    def resolve_for_context(
        self, ctx: BotProvisioningContext
    ) -> EngineProvisioningStrategy:
        """Resolve a strategy for legacy call sites with partial metadata.

        Prefer ``active_engine``.  If older call sites only pass template_type,
        route known coding templates to the coding strategy so historical
        TemplateService signatures continue to work while keeping the rule in
        one place.
        """
        if ctx.active_engine:
            return self.resolve(ctx.active_engine)
        if ctx.template_type in CODING_TEMPLATE_TYPES:
            return self.resolve("aicoding")
        return self._default


@lru_cache(maxsize=1)
def get_engine_provisioning_registry() -> EngineProvisioningRegistry:
    registry = EngineProvisioningRegistry()
    registry.register(AicodingProvisioningStrategy("aicoding"))
    registry.register(AicodingProvisioningStrategy("claude_code"))
    for engine_type in ("openclaw", "teclaw", "moltis", "hermes"):
        registry.register(DefaultProvisioningStrategy(engine_type))
    return registry


__all__ = [
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "get_engine_provisioning_registry",
]
