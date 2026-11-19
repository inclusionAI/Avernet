"""The one rule for "which catalog entry does this spawn name belong to?".

Two layers ask it and must not answer differently: spawn dispatch
(``runtime/pool``, so a spawn inherits its catalog entry's kind, engine and
manifest) and the run read surface (so a spawned agent is reported against the
entry it came from). A spawned agent is named
``{catalog_name}_{agent_id[:12]}``, so the entry is found by exact name first and
then by prefix.

Known limitation, carried deliberately: the prefix pass returns the **first**
matching entry, so with catalogs ``coder`` and ``coder_rust`` a spawn named
``coder_rust_ab12`` resolves to whichever of the two appears first in the
catalog — the answer depends on roster order, not on specificity. That predates
this helper and is runtime behaviour, so fixing it (preferring the longest
prefix) is a separate change; sharing the rule here at least guarantees that
display and dispatch cannot disagree about it, and
``tests/.../test_catalog_rule.py`` pins today's answer so the fix is a visible
test change rather than a silent behaviour shift.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.agentswarm.domain import models as dm


def find_catalog_entry(
    catalog: "tuple[dm.AgentSpec, ...] | list[dm.AgentSpec]", name: str,
) -> "dm.AgentSpec | None":
    """The catalog entry ``name`` belongs to, or ``None``."""
    for spec in catalog:
        if spec.name == name:
            return spec
    for spec in catalog:
        if name.startswith(spec.name):
            return spec
    return None
