"""``manifest.engine_config`` → ``DesiredEngineConfig``: the mapping, carried; nothing applies it yet (G-4).

Yes: converted, and never materialised. Every engine's capability verdict
marks ``engine_config`` unsupported today (``REASON_ENGINE_CONFIG``), so the
orchestrator reports the construct ``skipped`` with that reason before any
materialiser is asked, and no materialiser is registered for it anyway. The
compiler still maps it so the desired state is total — what the document
declares is what the report answers for, even when the answer is "not here" —
and so that #2858 (a materialiser for an allowlist of keys) is a materialiser
away, not a grammar change.
"""

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
