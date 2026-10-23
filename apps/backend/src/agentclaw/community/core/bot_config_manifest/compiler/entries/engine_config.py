"""``manifest.engine_config`` → ``DesiredEngineConfig``: the mapping, carried; nothing applies it yet (G-4)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import Declaration, DesiredEngineConfig, EntryFailure
from agentclaw.community.core.apply_engine.outcomes import entry_identity

__all__ = ["compile_engine_config"]


def compile_engine_config(raw: Sequence[Any] | Mapping[str, Any] | None) -> Declaration[DesiredEngineConfig]:
    """The validated form is one mapping with a ``config`` member.

    A list, or a mapping without ``config``, is a document that skipped the
    validator; each element is refused by the name it would report under.
    """
    if raw is None:
        return Declaration(())
    if isinstance(raw, Mapping):
        config = raw.get("config")
        if isinstance(config, Mapping):
            return Declaration((DesiredEngineConfig(config),))
        reason = "'engine_config.config' must be a mapping"
        return Declaration((), failures=(EntryFailure(DesiredEngineConfig.identity, reason),))
    reason = "'engine_config' must be a mapping with a 'config'"
    return Declaration((), failures=tuple(EntryFailure(entry_identity(e, i), reason) for i, e in enumerate(raw)))
