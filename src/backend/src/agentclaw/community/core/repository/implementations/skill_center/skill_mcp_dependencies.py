"""Read one ``ac_skill`` row's declared MCP dependencies.

A Skill's dependencies are part of the runtime MCP set: the projection folds
them into the codes it delivers to the device. So a command that adds or
removes a Skill changes the MCP set too, and can only declare an accurate
``ProjectionScope`` if it knows which codes moved.

That read belongs here rather than inside each command: the stored column is
a JSON string, and decoding it in three places is three chances to disagree
with the projection about which rows count.
"""

from __future__ import annotations

import json

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)


def skill_mcp_dependency_codes(skill: Skill | None) -> frozenset[str]:
    """The MCP server codes ``skill`` declares, empty when it declares none.

    Normalized through ``mcp_dependency_codes``, the same decoder the runtime
    projection uses, so a Skill mutation scopes exactly the codes the
    projection will resolve for it.

    A malformed row raises rather than reading as "no dependencies": the
    projection decodes the same column and would fail on it moments later,
    and failing here instead rolls the mutation back — desired state and
    runtime stay in agreement rather than diverging on a bad row.
    """
    if skill is None:
        return frozenset()
    raw = skill.mcp_dependencies
    if not raw:
        return frozenset()
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    return frozenset(mcp_dependency_codes(decoded or ()))


__all__ = ["skill_mcp_dependency_codes"]
