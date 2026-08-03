"""ORM persistence for Local Skill obsolete-package cleanup work."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.skill_center.local_skill_cleanup import (
    LocalSkillCleanupRepository,
    LocalSkillCleanupWorkModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SqlLocalSkillCleanupRepository(LocalSkillCleanupRepository):
    """Persist one retryable record per exact Bot scope and stale locator."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def record_pending(self, *, env: str, owner_id: str, bot_id: str, skill_id: str, package_locator: str) -> bool:
        with self._db.orm_session() as db:
            row = db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.package_locator == package_locator,
            ).one_or_none()
            if row is None:
                db.add(LocalSkillCleanupWorkModel(
                    env=env, owner_id=owner_id, bot_id=bot_id,
                    skill_id=int(skill_id), package_locator=package_locator,
                ))
            return True
