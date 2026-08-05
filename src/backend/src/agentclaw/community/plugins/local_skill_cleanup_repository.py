"""ORM persistence for Local Skill obsolete-package cleanup work."""

from __future__ import annotations

from hashlib import sha256

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

    def record_preparing(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
    ) -> int | None:
        """Persist quarantine identity before the authoritative package moves."""
        locator_hash = self._locator_hash(package_locator)
        with self._db.orm_session() as db:
            row = db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.package_locator_hash == locator_hash,
            ).one_or_none()
            if row is None:
                row = LocalSkillCleanupWorkModel(
                    env=env, owner_id=owner_id, bot_id=bot_id,
                    skill_id=int(skill_id), package_locator=package_locator,
                    package_locator_hash=locator_hash,
                    requires_runtime_restore=False, status="preparing",
                )
                db.add(row)
                db.flush()
            elif row.package_locator != package_locator:
                raise ValueError("Local Skill cleanup package locator hash collision")
            elif row.status != "preparing":
                raise ValueError("Local Skill cleanup locator is already in use")
            return int(row.id)

    def record_pending(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
        requires_runtime_restore: bool,
    ) -> int | None:
        locator_hash = self._locator_hash(package_locator)
        with self._db.orm_session() as db:
            row = db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.package_locator_hash == locator_hash,
            ).one_or_none()
            if row is None:
                row = LocalSkillCleanupWorkModel(
                    env=env, owner_id=owner_id, bot_id=bot_id,
                    skill_id=int(skill_id), package_locator=package_locator,
                    package_locator_hash=locator_hash,
                    requires_runtime_restore=requires_runtime_restore,
                )
                db.add(row)
                db.flush()
            elif row.package_locator != package_locator:
                raise ValueError("Local Skill cleanup package locator hash collision")
            else:
                row.requires_runtime_restore = bool(
                    row.requires_runtime_restore or requires_runtime_restore
                )
                row.status = "pending"
                row.last_error = None
                row.cleaned_at = None
            return int(row.id)

    def record_repair_required(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
    ) -> int | None:
        """Retain the only complete quarantine copy until package repair succeeds."""
        locator_hash = self._locator_hash(package_locator)
        with self._db.orm_session() as db:
            row = db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.package_locator_hash == locator_hash,
            ).one_or_none()
            if row is None:
                row = LocalSkillCleanupWorkModel(
                    env=env, owner_id=owner_id, bot_id=bot_id,
                    skill_id=int(skill_id), package_locator=package_locator,
                    package_locator_hash=locator_hash,
                    status="repair_required",
                    last_error="authoritative package repair required",
                )
                db.add(row)
                db.flush()
            elif row.package_locator != package_locator:
                raise ValueError("Local Skill cleanup package locator hash collision")
            else:
                row.status = "repair_required"
                row.last_error = "authoritative package repair required"
            return int(row.id)

    @staticmethod
    def _locator_hash(package_locator: str) -> str:
        return sha256(package_locator.encode("utf-8")).hexdigest()

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
                    "skill_id": str(row.skill_id),
                    "package_locator": row.package_locator,
                    "requires_runtime_restore": bool(row.requires_runtime_restore),
                }
                for row in rows
            ]

    def list_repair_required(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
    ) -> list[dict]:
        with self._db.orm_session() as db:
            rows = db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.skill_id == int(skill_id),
                LocalSkillCleanupWorkModel.status == "repair_required",
            ).order_by(LocalSkillCleanupWorkModel.id.asc()).all()
            return [
                {
                    "id": row.id,
                    "package_locator": row.package_locator,
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

    def cancel_pending(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str
    ) -> bool:
        with self._db.orm_session() as db:
            return db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.id == work_id,
                LocalSkillCleanupWorkModel.env == env,
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.status.in_(
                    ("pending", "preparing", "repair_required")
                ),
            ).update(
                {"status": "cancelled", "last_error": "cleanup target became authoritative"},
                synchronize_session=False,
            ) == 1
