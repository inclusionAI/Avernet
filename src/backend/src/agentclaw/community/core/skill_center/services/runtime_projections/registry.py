"""Resolve the ``EngineRuntimeProjection`` for a Bot's engine."""

from __future__ import annotations

from collections.abc import Mapping

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    EngineRuntimeProjection,
)
from agentclaw.community.log import get_logger


logger = get_logger()


class EngineRuntimeProjectionRegistry:
    """Map ``ac_bots.active_engine`` to the runtime contract it obeys.

    Keyed on the engine rather than the device provider deliberately. The
    engine is already on the Bot row by the time a plan resolves, whereas
    resolving a provider means ``DeviceContextResolver.resolve_for_bot`` — a
    binding query, a blocking ws-info HTTP call, and a second Bot query —
    which ``SkillSetService.sync_runtime`` then performs again, and which
    raises ``DeviceNotBoundError`` for an unbound Bot where the projector
    today degrades through a falsy ``sync_runtime`` instead.

    ``default`` is the per-domain contract, not a fourth enumerated key. Most
    engines want it, so registering is what marks an engine as *unusual*: a
    new ordinary engine needs no entry at all, and getting an engine wrong
    requires writing a wrong entry rather than forgetting a right one.
    """

    def __init__(
        self,
        *,
        default: EngineRuntimeProjection,
        by_engine: Mapping[str, EngineRuntimeProjection] | None = None,
    ) -> None:
        self._default = default
        self._by_engine = dict(by_engine or {})

    def for_engine(self, engine: str) -> EngineRuntimeProjection:
        """The runtime contract ``engine`` obeys; the default if unregistered."""
        projection = self._by_engine.get(engine)
        logger.info(
            "[EngineRuntimeProjectionRegistry] engine=%s resolved to %s%s",
            engine,
            type(projection or self._default).__name__,
            "" if projection is not None else " (default)",
        )
        return projection if projection is not None else self._default


__all__ = ["EngineRuntimeProjectionRegistry"]
