"""Wire projections owned by the SkillSet persistence boundary."""

from __future__ import annotations

from agentclaw.community.core.models.skill import SkillSet


def skill_set_item(row: SkillSet) -> dict:
    """Project a persistence row to the stable SkillSet read contract."""
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "is_default": bool(row.is_default),
        "is_builtin": bool(row.is_builtin),
        "is_active": True if row.is_default else bool(row.is_active),
        "user_id": row.user_id,
        "bolt_id": row.bolt_id,
        "engine_type": row.engine_type,
        "gmt_created": row.gmt_created.isoformat() if row.gmt_created else "",
        "gmt_modified": row.gmt_modified.isoformat() if row.gmt_modified else "",
        "env": row.env,
        "type": "default" if row.is_default else "custom",
    }
