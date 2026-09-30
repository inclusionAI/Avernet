"""Tenant-scoped address lookup for deprecated SkillSet wires."""

from __future__ import annotations

from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetScope,
)


class LegacySkillSetScopeQueries:
    """Repository mixin that exposes no SkillSet content before authorization."""

    def resolve_legacy_set_scope(
        self, *, set_id: str
    ) -> LegacySkillSetScope | None:
        with self._db.orm_session() as session:
            row = (
                self._scope(session.query(SkillSet), SkillSet)
                .filter(SkillSet.id == int(set_id))
                .one_or_none()
            )
            if row is None:
                raise SkillSetControlPlaneNotFoundError()
            if row.is_default:
                return None
            if not row.user_id or not row.bolt_id:
                raise SkillSetControlPlaneNotFoundError()
            return LegacySkillSetScope(
                owner_id=str(row.user_id),
                bot_id=str(row.bolt_id),
            )


__all__ = ["LegacySkillSetScopeQueries"]
