"""Legacy SkillSet collections behavior."""

from datetime import datetime
from typing import Any

from agentclaw.community.core.skill_center.utils.skill_metadata_writer import (
    SkillSetMetadataWriter,
)

from .skill_set_context import (
    logger,
)


class SkillSetCollections:
    def _validate_name(self, name: str) -> None:
        """Validate skill set name (cannot contain underscore)."""
        if "_" in name:
            raise ValueError("Skill set name cannot contain underscore '_'")

    def create_skill_set(
        self,
        name: str,
        description: str | None = None,
        user_id: str | None = None,
        is_default: bool = False,
        bolt_id: str | None = None,
    ) -> dict:
        """Create a new skill set.

        Args:
            name: Skill set name (cannot contain underscore)
            description: Optional description
            user_id: User ID (optional in Phase 1)
            is_default: Whether this is the default skill set
            bolt_id: Bot ID, defaults to self.bot_id if not provided

        Returns:
            Created SkillSet dict

        Raises:
            ValueError: If name contains underscore or already exists
        """
        self._validate_name(name)

        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        # Check if name already exists for the same user (排除已删除 Bot 的技能集)
        if hasattr(self.skill_set_repo, "list_all_exclude_deleted"):
            existing_sets = self.skill_set_repo.list_all_exclude_deleted(
                user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type
            )
        else:
            existing_sets = self.skill_set_repo.list_all(
                user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type
            )
        for existing in existing_sets:
            if existing.get("name") == name:
                raise ValueError(
                    f"Skill set name '{name}' already exists for this user"
                )

        # 检查是否有已删除 Bot 的同名技能集，如果有就复用（避免唯一约束冲突）
        existing_deleted = None
        if hasattr(self.skill_set_repo, "get_skill_set_by_name_include_deleted"):
            existing_deleted = (
                self.skill_set_repo.get_skill_set_by_name_include_deleted(
                    name, user_id, effective_bolt_id
                )
            )

        if existing_deleted:
            update_data = {
                "bolt_id": effective_bolt_id,
                "engine_type": self.engine_type,
                "description": description,
                "is_default": is_default,
                "is_active": 1,
                "gmt_modified": datetime.utcnow(),
            }
            logger.info(
                f"[create_skill_set] Reusing skill set from deleted bot: {name}"
            )
            skill_set = self.skill_set_repo.update(existing_deleted["id"], update_data)
        else:
            skill_set_data = {
                "name": name,
                "description": description,
                "user_id": user_id,
                "is_default": is_default,
                "is_active": 1,
                "is_builtin": False,
                "bolt_id": effective_bolt_id,
                "engine_type": self.engine_type,
                "gmt_created": datetime.utcnow(),
                "gmt_modified": datetime.utcnow(),
            }
            skill_set = self.skill_set_repo.create(skill_set_data)

        # Update metadata file
        logger.info(
            f"[create_skill_set] Writing metadata for new skill set: {skill_set['name']}"
        )
        SkillSetMetadataWriter(
            skill_set_repo=self.skill_set_repo,
            skill_repo=self.skill_repo,
            skills_dir=self.skills_dir,
            user_id=user_id,
            bot_id=self.bot_id,
        ).write_metadata()

        return skill_set

    def get_skill_set(
        self, skill_set_id: str, user_id: str | None = None
    ) -> dict | None:
        """Get a skill set by ID.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check (optional in Phase 1)

        Returns:
            SkillSet dict or None
        """
        return self.skill_set_repo.get_by_id(skill_set_id)

    def get_user_default_enabled(self, user_id: str | None, bolt_id: str) -> int | None:
        """查询用户对默认能力集的启用状态

        Returns:
            None: 无记录，表示默认启用
            1: 已启用
            0: 已禁用
        """
        return self.skill_set_repo._get_user_default_enabled(
            user_id, bolt_id, engine_type=self.engine_type
        )

    def list_skill_sets(
        self, user_id: str | None = None, bolt_id: str | None = None
    ) -> list[dict]:
        """List all skill sets.

        Args:
            user_id: User ID for filtering (optional in Phase 1)
            bolt_id: Bot ID, defaults to self.bot_id if not provided

        Returns:
            List of SkillSet dicts
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        selections = self._ordered_active_default_selections(
            bolt_id=effective_bolt_id, engine_type=self.engine_type
        )
        if selections is None:
            return self.skill_set_repo.list_all(
                user_id,
                bolt_id=effective_bolt_id,
                engine_type=self.engine_type,
            )

        first_result: list[dict] | None = None
        for selection in selections:
            result = self.skill_set_repo.list_all(
                user_id=user_id,
                bolt_id=effective_bolt_id,
                engine_type=self.engine_type,
                **self._default_skill_set_query_kwargs(self.engine_type, selection),
            )
            if first_result is None:
                first_result = result
            if self._has_default_skill_set(result):
                return result
        return first_result or []

    def update_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        is_default: bool | None = None,
        bolt_id: str | None = None,
    ) -> dict | None:
        """Update a skill set.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check
            name: New name (optional)
            description: New description (optional)
            is_default: New default status (optional)
            bolt_id: Bot ID, defaults to self.bot_id if not provided

        Returns:
            Updated SkillSet dict or None

        Raises:
            ValueError: If name contains underscore or is already taken
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return None

        # Cannot update builtin skill sets (except description)
        if skill_set.get("is_builtin") and name is not None:
            raise ValueError("Cannot modify builtin skill set name")

        update_data = {"gmt_modified": datetime.utcnow()}

        if name is not None:
            self._validate_name(name)
            # Check name uniqueness for the same user (排除已删除 Bot 的技能集)
            if hasattr(self.skill_set_repo, "list_all_exclude_deleted"):
                existing_sets = self.skill_set_repo.list_all_exclude_deleted(
                    user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type
                )
            else:
                existing_sets = self.skill_set_repo.list_all(
                    user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type
                )
            for existing in existing_sets:
                if existing.get("name") == name and existing.get("id") != skill_set_id:
                    raise ValueError(
                        f"Skill set name '{name}' already exists for this user"
                    )
            update_data["name"] = name

        if description is not None:
            update_data["description"] = description

        if is_default is not None:
            update_data["is_default"] = is_default

        updated = self.skill_set_repo.update(skill_set_id, update_data)

        # Update metadata file
        SkillSetMetadataWriter(
            skill_set_repo=self.skill_set_repo,
            skill_repo=self.skill_repo,
            skills_dir=self.skills_dir,
            user_id=user_id,
            bot_id=self.bot_id,
        ).write_metadata()

        return updated

    def delete_skill_set(self, skill_set_id: str, user_id: str | None = None) -> bool:
        """Delete a skill set.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If trying to delete a builtin/default/active skill set
        """
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return False

        if skill_set.get("is_builtin"):
            raise ValueError("Cannot delete builtin skill set")

        if skill_set.get("is_default"):
            raise ValueError("默认技能集不允许删除")

        if skill_set.get("is_active"):
            raise ValueError("当前激活的技能集不允许删除，请先切换到其他技能集")

        result = self.skill_set_repo.delete(skill_set_id)

        # Update metadata file
        SkillSetMetadataWriter(
            skill_set_repo=self.skill_set_repo,
            skill_repo=self.skill_repo,
            skills_dir=self.skills_dir,
            user_id=user_id,
            bot_id=self.bot_id,
        ).write_metadata()

        return result

    def _create_skill_from_market(
        self,
        market_skill: dict[str, Any],
        user_id: str | None = None,
        bolt_id: str | None = None,
    ):
        """Create a skill record from market (git repo) data."""
        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        # Get full path from market skill and convert to relative path
        full_path = market_skill.get("full_path", "")
        if full_path:
            # Extract relative path from skills-repo directory
            # Use _get_market_repo_dir() which returns global_repo_dir in cloud mode
            # This matches the path used in _convert_skill_info_to_dict for full_path generation
            market_repo_dir = str(self.skill_service._get_market_repo_dir())
            if full_path.startswith(market_repo_dir):
                rel_path = full_path[len(market_repo_dir) :].lstrip("/")
                git_path = f"git://{rel_path}"
            else:
                # Fallback to using link_name which contains the full relative path
                # link_name format: category_subcategory_skill-id -> category/subcategory/skill-id
                link_name = market_skill.get("link_name", "")
                if link_name and "_" in link_name:
                    # link_name format: category_subcategory_skill-id
                    parts = link_name.split("_", 2)
                    if len(parts) >= 3:
                        rel_path = f"{parts[0]}/{parts[1]}/{market_skill['id']}"
                    else:
                        rel_path = market_skill["id"]
                else:
                    rel_path = market_skill["id"]
                git_path = f"git://{rel_path}"
        else:
            # Fallback to short id
            git_path = f"git://{market_skill['id']}"

        # Check if skill already exists by path
        existing_skills = self.skill_repo.list_skills(bolt_id=effective_bolt_id)
        for existing in existing_skills:
            if existing.get("git_path") == git_path:
                return existing

        # Create new skill using repository
        # Note: id is auto-increment, don't specify it
        skill_data = {
            "name": market_skill["name"],
            "description": market_skill.get("description", ""),
            "git_path": git_path,
            "category": market_skill.get("category", "general"),
            "tags": "[]",
            "input_schema": "",
            "output_schema": "",
            "is_public": True,
            "is_builtin": False,
            "user_id": user_id,
            "bolt_id": effective_bolt_id,
            "gmt_created": datetime.utcnow(),
            "gmt_modified": datetime.utcnow(),
        }
        return self.skill_repo.create(skill_data)

    def resolve_or_create_legacy_market_skill(
        self, *, identifier: str, owner_id: str, bot_id: str
    ) -> str:
        """Resolve the legacy name/path wire and materialize a Repo Skill if absent."""
        market_skill = next(
            (
                item
                for item in self.skill_service.get_skills_in_path("")
                if item.get("id") == identifier
                or str(item.get("path") or "").endswith(identifier)
            ),
            None,
        )
        if market_skill is None:
            raise ValueError("Skill not found")
        return str(self._create_skill_from_market(market_skill, owner_id, bot_id)["id"])

    def get_set_skills(
        self, skill_set_id: str, user_id: str | None = None
    ) -> list[dict]:
        """Get all skills in a skill set.

        For default skill sets, filters out user-excluded skills from
        ac_default_skillset_skill_exclusion.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for permission check and exclusion lookup

        Returns:
            List of Skill dicts
        """
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return []

        skills = self.skill_set_repo.get_skills_in_set(skill_set_id)

        # Default skill set: filter out user-excluded skills
        # (mirrors get_set_mcp_servers which filters ac_default_skillset_mcp_exclusion)
        # The Bot owner, matching how the exclusion is written and read.
        exclusion_owner_id = self.entity_id or user_id
        if skill_set.get("is_default") and exclusion_owner_id and self.bot_id:
            excluded = self.skill_set_repo.get_excluded_skills(
                user_id=exclusion_owner_id,
                bot_id=self.bot_id,
                skill_set_id=int(skill_set_id),
            )
            excluded_ids = set(excluded)
            if excluded_ids:
                # skill id from _skill_to_dict is str, excluded_ids from DB are int
                skills = [s for s in skills if int(s.get("id", 0)) not in excluded_ids]

        return skills

    def ensure_default_skill_set(
        self, user_id: str | None = None, bolt_id: str | None = None
    ) -> dict:
        """Ensure default skill set exists.

        Args:
            user_id: User ID (optional in Phase 1)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            Default SkillSet dict
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id

        # Look for existing default
        default = self.skill_set_repo.get_default(
            user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type
        )

        if default:
            return default

        # Create default skill set
        return self.create_skill_set(
            name="默认技能集",
            description="系统默认技能集，用户可以根据需要添加或移除技能",
            user_id=user_id,
            is_default=True,
            bolt_id=effective_bolt_id,
        )

    def get_default_skill_set(
        self, user_id: str | None = None, bolt_id: str | None = None
    ) -> dict | None:
        """Get default skill set.

        Args:
            user_id: User ID (optional in Phase 1)
            bolt_id: Bot ID，为空时默认使用 self.bot_id

        Returns:
            Default SkillSet dict or None
        """
        effective_bolt_id = bolt_id if bolt_id else self.bot_id
        return self.skill_set_repo.get_default(
            user_id, bolt_id=effective_bolt_id, engine_type=self.engine_type
        )
