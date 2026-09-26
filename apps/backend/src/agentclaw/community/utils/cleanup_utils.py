"""
清理已删除 Bot 的脏数据工具类。

功能:
- 查询指定用户的已删除 Bot
- 清理关联的技能数据 (ac_skill)
- 清理关联的技能集数据 (ac_skill_set, ac_skill_set_skill, ac_skill_set_mcp_server)
- 清理关联的资源数据 (ac_resource)
- 清理物理文件 (bolt_data 目录)

使用:
    from agentclaw.community.utils.cleanup_utils import CleanupService
    svc = CleanupService(db=my_database_plugin)
    results = svc.cleanup_user_data(["user1"], dry_run=True)

Rule 14: dialect-agnostic — uses ``DatabasePlugin.orm_session()`` which
yields a SQLAlchemy ``Session`` against both SQLite (local) and ZDAS (prod).
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass
class CleanupResult:
    """单个用户的清理结果"""

    user_id: str
    deleted_bots: List[Dict[str, Any]] = field(default_factory=list)
    skills_deleted: int = 0
    skill_sets_deleted: int = 0
    resources_deleted: int = 0
    files_deleted: int = 0
    errors: List[str] = field(default_factory=list)
    dry_run: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "deleted_bots_count": len(self.deleted_bots),
            "deleted_bots": [b.get("bot_id") for b in self.deleted_bots],
            "skills_deleted": self.skills_deleted,
            "skill_sets_deleted": self.skill_sets_deleted,
            "resources_deleted": self.resources_deleted,
            "files_deleted": self.files_deleted,
            "errors": self.errors,
            "dry_run": self.dry_run,
        }


class CleanupService:
    """清理已删除 Bot 的脏数据。

    所有数据库操作通过 ``DatabasePlugin`` 注入。Dialect-agnostic — uses
    ``orm_session()`` which yields a SQLAlchemy ``Session`` against both
    SQLite (local) and ZDAS (prod). No per-dialect branching.
    """

    def __init__(self, db: DatabasePlugin):
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_deleted_bots_by_users(
        self, user_ids: List[str], env: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """获取指定用户的已删除 Bot 列表。"""
        result: Dict[str, List[Dict[str, Any]]] = {}
        for user_id in user_ids:
            bots = self._get_deleted_bots_for_user(user_id, env)
            if bots:
                result[user_id] = bots
        return result

    def cleanup_single_bot_data(
        self, bot_id: str, user_id: str, dry_run: bool = False
    ) -> Dict[str, Any]:
        """清理单个 Bot 的关联数据 (技能/技能集/资源)。"""
        logger.info(
            f"[CleanupService] Cleaning single bot: bot_id={bot_id}, "
            f"user_id={user_id}, dry_run={dry_run}"
        )
        result: Dict[str, Any] = {
            "bot_id": bot_id,
            "user_id": user_id,
            "skills_deleted": 0,
            "skill_sets_deleted": 0,
            "resources_deleted": 0,
            "errors": [],
        }
        bot_ids = [bot_id]

        for label, fn in [
            ("skills", self._cleanup_skills),
            ("skill_sets", self._cleanup_skill_sets),
            ("resources", self._cleanup_resources),
        ]:
            try:
                result[f"{label}_deleted"] = fn(bot_ids, dry_run)
            except Exception as e:
                msg = f"Cleanup {label} error for bot {bot_id}: {e}"
                logger.error(f"[CleanupService] {msg}")
                result["errors"].append(msg)

        return result

    def cleanup_user_data(
        self,
        user_ids: List[str],
        dry_run: bool = True,
        include_files: bool = False,
        env: Optional[str] = None,
        bolt_data_root: Optional[Path] = None,
    ) -> List[CleanupResult]:
        """清理指定用户的已删除 Bot 脏数据。

        Args:
            user_ids: 用户 ID 列表
            dry_run: 预览模式 (不执行删除)
            include_files: 是否删除物理文件
            env: 指定环境 (pre/prod)，None 则使用当前环境
            bolt_data_root: 物理文件根目录。仅 include_files=True 时需要。
        """
        results = []
        for user_id in user_ids:
            result = self._cleanup_single_user(
                user_id, dry_run, include_files, env, bolt_data_root
            )
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Internal: 获取已删除 Bot
    # ------------------------------------------------------------------

    def _get_deleted_bots_for_user(
        self, user_id: str, env: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        target_env = env or get_current_env()
        from agentclaw.community.plugin_api.models import BotModel

        with self._db.orm_session() as s:
            rows = (
                s.query(BotModel)
                .filter(
                    BotModel.owner_id == user_id,
                    BotModel.is_delete == 1,
                    BotModel.bot_id != "default",
                    BotModel.env == target_env,
                )
                .all()
            )
            return [self._bot_to_dict(b) for b in rows]

    # ------------------------------------------------------------------
    # Internal: 清理技能 (ac_skill)
    # ------------------------------------------------------------------

    def _cleanup_skills(
        self, bot_ids: List[str], dry_run: bool, env: Optional[str] = None
    ) -> int:
        if not bot_ids:
            return 0
        from agentclaw.community.core.models.skill import Skill

        return self._delete_by_bolt_ids(
            Skill, bot_ids, env or get_current_env(), dry_run,
        )

    # ------------------------------------------------------------------
    # Internal: 清理技能集 (ac_skill_set + 关联表)
    # ------------------------------------------------------------------

    def _cleanup_skill_sets(
        self, bot_ids: List[str], dry_run: bool, env: Optional[str] = None
    ) -> int:
        if not bot_ids:
            return 0
        from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill

        target_env = env or get_current_env()
        with self._db.orm_session() as s:
            q = s.query(SkillSet).filter(
                SkillSet.bolt_id.in_(bot_ids), SkillSet.env == target_env,
            )
            count = q.count()
            if count > 0 and not dry_run:
                ss_ids = [row.id for row in q.all()]
                if ss_ids:
                    # Association tables: delete by skill_set_id. Use ORM
                    # where the table exists; for the legacy mcp_server
                    # alias we run a tolerant raw-SQL ``DELETE`` so a
                    # missing table doesn't break cleanup.
                    s.query(SkillSetSkill).filter(
                        SkillSetSkill.skill_set_id.in_(ss_ids)
                    ).delete(synchronize_session=False)
                    self._tolerant_delete_legacy(
                        s, "ac_skill_set_mcp_server", "skill_set_id", ss_ids,
                    )
                q.delete(synchronize_session=False)
        self._log_cleanup("ac_skill_set", count, dry_run, bot_ids)
        return count

    # ------------------------------------------------------------------
    # Internal: 清理资源 (ac_resource)
    # ------------------------------------------------------------------

    def _cleanup_resources(
        self, bot_ids: List[str], dry_run: bool, env: Optional[str] = None
    ) -> int:
        if not bot_ids:
            return 0
        from agentclaw.community.plugin_api.models import ResourceModel

        return self._delete_by_bolt_ids(
            ResourceModel, bot_ids, env or get_current_env(), dry_run,
        )

    # ------------------------------------------------------------------
    # Internal: 清理物理文件
    # ------------------------------------------------------------------

    def _cleanup_physical_files(
        self,
        deleted_bots: List[Dict[str, Any]],
        dry_run: bool,
        bolt_data_root: Optional[Path] = None,
    ) -> int:
        deleted_count = 0
        for bot in deleted_bots:
            entity_type = bot.get("entity_type", "staff")
            entity_id = bot.get("entity_id", "")
            bot_id = bot.get("bot_id", "")
            if not entity_id or not bot_id:
                continue

            if bolt_data_root:
                bot_data_path = bolt_data_root / f"{entity_type}_{entity_id}" / bot_id
            else:
                from agentclaw.community.core.storage import path as storage_path

                rel = storage_path.get_bolt_data_path(
                    entity_type=entity_type, entity_id=entity_id, bot_id=bot_id
                )
                bot_data_path = Path("/") / rel

            if not bot_data_path.exists():
                logger.debug(f"[CleanupService] Bot data dir not found: {bot_data_path}")
                continue

            logger.info(
                f"[CleanupService] {'Would delete' if dry_run else 'Deleting'} "
                f"bot data dir: {bot_data_path}"
            )
            if not dry_run:
                try:
                    shutil.rmtree(bot_data_path, ignore_errors=True)
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"[CleanupService] Failed to delete {bot_data_path}: {e}")

        return deleted_count

    # ------------------------------------------------------------------
    # Internal: 批量清理用户
    # ------------------------------------------------------------------

    def _cleanup_single_user(
        self,
        user_id: str,
        dry_run: bool,
        include_files: bool,
        env: Optional[str],
        bolt_data_root: Optional[Path],
    ) -> CleanupResult:
        result = CleanupResult(user_id=user_id, dry_run=dry_run)

        deleted_bots = self._get_deleted_bots_for_user(user_id, env)
        if not deleted_bots:
            logger.info(f"[CleanupService] No deleted bots for user {user_id}")
            return result

        result.deleted_bots = deleted_bots
        bot_ids = [b.get("bot_id") for b in deleted_bots]
        logger.info(
            f"[CleanupService] Found {len(bot_ids)} deleted bots for user {user_id}"
        )

        for label, fn in [
            ("skills", self._cleanup_skills),
            ("skill_sets", self._cleanup_skill_sets),
            ("resources", self._cleanup_resources),
        ]:
            try:
                setattr(result, f"{label}_deleted", fn(bot_ids, dry_run, env))
            except Exception as e:
                msg = f"Cleanup {label} error for user {user_id}: {e}"
                logger.error(f"[CleanupService] {msg}", exc_info=True)
                result.errors.append(msg)

        if include_files:
            try:
                result.files_deleted = self._cleanup_physical_files(
                    deleted_bots, dry_run, bolt_data_root
                )
            except Exception as e:
                msg = f"Cleanup files error for user {user_id}: {e}"
                logger.error(f"[CleanupService] {msg}", exc_info=True)
                result.errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _delete_by_bolt_ids(
        self, model, bot_ids: List[str], target_env: str, dry_run: bool,
    ) -> int:
        """Count + bulk-delete rows matching ``bolt_id IN bot_ids AND env``."""
        with self._db.orm_session() as s:
            q = s.query(model).filter(
                model.bolt_id.in_(bot_ids), model.env == target_env,
            )
            count = q.count()
            if count > 0 and not dry_run:
                q.delete(synchronize_session=False)
        self._log_cleanup(model.__tablename__, count, dry_run, bot_ids)
        return count

    @staticmethod
    def _tolerant_delete_legacy(
        session, table: str, col: str, ids: List[Any],
    ) -> None:
        """Best-effort delete for legacy tables that may not exist.

        ``ac_skill_set_mcp_server`` is a legacy alias of the migrated
        ``ac_skill_set_mcp`` model; older deployments may still have rows
        in it. Use named bind params (``:p0`` etc) so the SQL is
        dialect-agnostic via SQLAlchemy ``text()``.
        """
        if not ids:
            return
        placeholders = ", ".join([f":p{i}" for i in range(len(ids))])
        params = {f"p{i}": v for i, v in enumerate(ids)}
        try:
            session.execute(
                text(f"DELETE FROM {table} WHERE {col} IN ({placeholders})"),
                params,
            )
        except Exception as e:
            msg = str(e).lower()
            if "no such table" in msg or "doesn't exist" in msg or "does not exist" in msg:
                logger.debug(
                    f"[CleanupService] Table {table} does not exist, skipping"
                )
            else:
                raise

    @staticmethod
    def _bot_to_dict(bot) -> Dict[str, Any]:
        return {
            "id": bot.id,
            "bot_id": bot.bot_id,
            "bot_name": bot.bot_name,
            "entity_id": bot.entity_id,
            "entity_type": bot.entity_type,
            "creator_id": bot.creator_id,
            "owner_id": bot.owner_id,
            "device_id": bot.device_id,
            "env": bot.env,
            "gmt_create": bot.gmt_create.isoformat() if hasattr(bot.gmt_create, "isoformat") else bot.gmt_create,
        }

    @staticmethod
    def _log_cleanup(table: str, count: int, dry_run: bool, bot_ids: List[str]):
        if count > 0:
            action = "Would delete" if dry_run else "Deleting"
            logger.info(
                f"[CleanupService] {action} {count} rows from {table} "
                f"for bots: {bot_ids}"
            )
