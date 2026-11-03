"""Legacy SkillSet views behavior."""

from typing import Any

from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
)

from .skill_set_context import (
    logger,
)


class SkillSetRuntimeViews:
    def get_all_skill_sets_with_skills(
        self, user_id: str | None = None, bolt_id: str | None = None
    ) -> list[dict[str, Any]]:
        """获取所有能力集（包括激活和非激活），每个能力集包含其技能列表

        Args:
            user_id: User ID for filtering (optional)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            List[Dict]: 能力集列表，每个能力集包含 skills 字段
            [
                {
                    "id": str,
                    "name": str,
                    "description": str,
                    "is_default": bool,
                    "is_builtin": bool,
                    "is_active": bool,
                    "user_id": str,
                    "bolt_id": str,
                    "env": str,
                    "gmt_created": str,
                    "gmt_modified": str,
                    "skills": [                 # 该能力集包含的技能列表
                        {
                            "id": str,
                            "name": str,
                            "description": str,
                            "git_path": str,
                            "category": str,
                            "tags": list,
                            ...
                        }
                    ],
                    "skill_count": int
                }
            ]
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        effective_user_id = user_id if user_id else self.user_id or self.entity_id

        logger.info(
            f"[get_all_skill_sets_with_skills] user_id={effective_user_id}, bolt_id={effective_bolt_id}"
        )

        # 1. 获取所有能力集（激活和非激活）
        skill_sets = self.skill_set_repo.list_all(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=self.engine_type,
        )

        logger.info(f"[get_all_skill_sets_with_skills] 找到 {len(skill_sets)} 个能力集")

        # 2. 为每个能力集查询技能
        for skill_set in skill_sets:
            skill_set_id = skill_set.get("id")
            skills = self.skill_set_repo.get_skills_in_set(str(skill_set_id))
            skill_set["skills"] = skills
            skill_set["skill_count"] = len(skills)
            logger.debug(
                f"[get_all_skill_sets_with_skills] 能力集 {skill_set.get('name')} 包含 {len(skills)} 个技能"
            )

        return skill_sets

    def get_all_skill_sets_with_mcps(
        self, user_id: str | None = None, bolt_id: str | None = None
    ) -> list[dict[str, Any]]:
        """获取所有能力集（包括激活和非激活）及其关联的 MCP 列表

        Args:
            user_id: User ID for filtering (optional)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            List[Dict]: 能力集列表，每个能力集包含 mcps 字段
            [
                {
                    "id": str,
                    "name": str,
                    "description": str,
                    "is_default": bool,
                    "is_builtin": bool,
                    "is_active": bool,
                    "user_id": str,
                    "bolt_id": str,
                    "bot_id": str,          # 同 bolt_id，用于前端兼容
                    "env": str,
                    "gmt_created": str,
                    "gmt_modified": str,
                    "mcps": [               # 该能力集关联的 MCP 列表
                        {
                            "id": str,
                            "server_code": str,
                            "name": str,
                            "description": str,
                            "icon": str,
                            "status": str
                        }
                    ],
                    "mcp_count": int        # MCP 数量
                }
            ]
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        # ``user_id`` is the caller in historical adapters. Installation and
        # Default exclusion state belong to the Bot owner carried by the
        # factory's entity_id, never to a collaborator acting on that Bot.
        effective_user_id = self.entity_id

        logger.info(
            f"[get_all_skill_sets_with_mcps] user_id={effective_user_id}, bolt_id={effective_bolt_id}"
        )

        # 1. 获取所有能力集
        skill_sets = self.skill_set_repo.list_all(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=self.engine_type,
        )

        # 排序：默认能力集在前，然后按创建时间排序
        skill_sets = sorted(
            skill_sets, key=lambda s: (not s.get("is_default"), s.get("gmt_created"))
        )

        logger.info(f"[get_all_skill_sets_with_mcps] 找到 {len(skill_sets)} 个能力集")

        # 2. 为每个能力集查询 MCP
        for skill_set in skill_sets:
            skill_set_id = skill_set.get("id")
            mcps = self.get_set_mcp_servers(
                str(skill_set_id), effective_user_id, effective_bolt_id
            )
            skill_set["mcps"] = mcps
            skill_set["mcp_count"] = len(mcps)
            # 添加 bot_id 字段（前端兼容）
            skill_set["bot_id"] = skill_set.get("bolt_id") or "default"
            logger.debug(
                f"[get_all_skill_sets_with_mcps] 能力集 {skill_set.get('name')} 包含 {len(mcps)} 个 MCP"
            )

        return skill_sets

    def list_active_skill_sets(
        self,
        *,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        """Return active SkillSets using the service-owned default lookup policy.

        This keeps delivery adapters from re-deriving default SkillSet
        compatibility kwargs while preserving historical query shape for engines
        that do not need a compatibility override.
        """
        effective_user_id = user_id if user_id is not None else self.entity_id
        effective_bolt_id = bolt_id if bolt_id is not None else self.bot_id
        effective_engine = engine_type if engine_type is not None else self.engine_type
        return self._get_all_active_skill_sets_with_default_fallback(
            user_id=effective_user_id,
            bolt_id=effective_bolt_id,
            engine_type=effective_engine,
        )

    def get_active_skills(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
    ) -> list[dict]:
        """The bot's active, de-duped skill DB records.

        Each record carries ``git_path`` — the source of truth for the skill's
        actual location (``git://<repo-rel>`` for shared market skills,
        ``local://<abs host path>`` for user uploads). Installation is the
        single source of truth: the reader flushes Set configuration
        (activation, Default membership, exclusions) into Installation, then
        answers from it alone.

        Used by both ``get_symlink_mappings`` (ARCA container symlinks) and the
        config-compose collector (teclaw — which reads ``git_path`` directly, no
        container round-trip); both consume only the published keys ``id``,
        ``name``, ``git_path``, ``skill_uuid``, ``sc_version_number``.
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        effective_user_id = user_id if user_id else self.entity_id
        if self._reader is None:
            raise RuntimeError(
                "SkillSetService.get_active_skills requires the capability "
                "state reader; construct through SkillSetServiceFactory"
            )
        try:
            assets = self._reader.active_skill_assets(
                bot_id=effective_bolt_id, owner_id=effective_user_id
            )
        except LocalSkillNotFoundError:
            # The legacy merge tolerated an unknown Bot by answering empty;
            # BFF display callers keep that grace.
            logger.warning(
                "[get_active_skills] Bot not found: user_id=%s, bolt_id=%s",
                effective_user_id,
                effective_bolt_id,
            )
            return []
        return [
            {
                "id": str(asset.skill_id),
                "name": asset.name,
                "git_path": asset.git_path,
                "skill_uuid": asset.skill_uuid,
                "sc_version_number": asset.sc_version_number,
            }
            for asset in assets
        ]
