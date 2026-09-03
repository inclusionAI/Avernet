"""Read one Skill's effective MCP dependencies for runtime projection.

A Skill's dependencies are part of the runtime MCP set: the projection folds
them into the codes it delivers to the device. So a command that adds or
removes a Skill changes the MCP set too, and can only declare an accurate
``ProjectionScope`` if it knows which codes moved.

Local and Repo Skills declare dependencies on ``ac_skill``. Center Skills are
versioned, so their authoritative dependencies live on the latest PUBLISHED
``ac_skill_version`` instead. That distinction belongs here rather than inside
each command: decoding it in several writers is several chances to disagree
with the runtime reader.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
    mcp_dependency_codes_from_version_metadata,
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


def skill_projection_mcp_dependency_codes(
    session: Session,
    skill: Skill | None,
    *,
    allow_unresolvable_center: bool = False,
) -> frozenset[str]:
    """Return the dependency codes the runtime reader resolves for ``skill``.

    The caller already holds the Skill row lock. Publication takes the same
    Skill-before-Version lock order, so selecting the latest PUBLISHED Version
    here cannot race a new Center publication into the command's projection
    scope. Missing or malformed Center Version metadata fails closed: treating
    it as dependency-free would commit desired state that the runtime reader
    cannot resolve consistently. Removal paths may opt into the legacy asset
    fallback so an already OFFLINE/unresolvable Center member never becomes
    impossible to clean up.
    """

    if skill is None:
        return frozenset()
    if not str(skill.git_path or "").startswith("center://"):
        return skill_mcp_dependency_codes(skill)

    def cleanup_fallback() -> frozenset[str]:
        try:
            return skill_mcp_dependency_codes(skill)
        except (TypeError, ValueError):
            return frozenset()

    version = (
        session.query(SkillVersion)
        .filter(
            SkillVersion.avernet_tenant == skill.avernet_tenant,
            SkillVersion.env == skill.env,
            SkillVersion.skill_id == int(skill.id),
            SkillVersion.status == "PUBLISHED",
        )
        .order_by(SkillVersion.version_ordinal.desc(), SkillVersion.id.desc())
        .with_for_update()
        .first()
    )
    if version is None or not version.metadata_json:
        if allow_unresolvable_center:
            return cleanup_fallback()
        raise ValueError("Center Skill has no PUBLISHED Version MCP metadata")
    try:
        return frozenset(
            mcp_dependency_codes_from_version_metadata(version.metadata_json)
        )
    except (TypeError, ValueError):
        if allow_unresolvable_center:
            return cleanup_fallback()
        raise


__all__ = [
    "skill_mcp_dependency_codes",
    "skill_projection_mcp_dependency_codes",
]
