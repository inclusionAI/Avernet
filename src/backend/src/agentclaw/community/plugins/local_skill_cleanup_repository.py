"""ORM persistence for Local Skill obsolete-package cleanup work."""

from __future__ import annotations

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.skill_center.local_skill_cleanup import (
    LocalSkillCleanupWorkModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.local_skill_cleanup import LocalSkillCleanupRepository


class SqlLocalSkillCleanupRepository(LocalSkillCleanupRepository):
    """Persist one retryable record per exact Bot scope and stale locator."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def record_pending(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
        requires_runtime_restore: bool,
    ) -> bool:
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
                    requires_runtime_restore=requires_runtime_restore,
                ))
            return True

    def list_pending(self, *, env: str, owner_id: str, bot_id: str) -> list[dict]:
        with self._db.orm_session() as db:
            rows = db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.status == "pending",
            ).order_by(LocalSkillCleanupWorkModel.id.asc()).all()
            return [
                {
                    "id": row.id,
                    "package_locator": row.package_locator,
                    "requires_runtime_restore": bool(row.requires_runtime_restore),
                }
                for row in rows
            ]

    def mark_cleaned(self, *, work_id: int, env: str, owner_id: str, bot_id: str) -> bool:
        with self._db.orm_session() as db:
            return db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.id == work_id,
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.status == "pending",
            ).update({"status": "cleaned", "cleaned_at": func.now()}, synchronize_session=False) == 1

    def mark_failed(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str, error: str
    ) -> bool:
        with self._db.orm_session() as db:
            return db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.id == work_id,
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.status == "pending",
            ).update(
                {
                    "attempts": LocalSkillCleanupWorkModel.attempts + 1,
                    "last_error": error,
                },
                synchronize_session=False,
            ) == 1
