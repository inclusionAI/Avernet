"""Legacy SkillSet mcps behavior."""

import zlib
from pathlib import Path
from typing import Any, List, Mapping, Optional

from agentclaw.community.core.devices.models import SynlinkMappingInfo
from agentclaw.community.core.mcp.services._defaults import (
    get_default_mcp_config,
    get_default_mcp_server_codes,
)
from agentclaw.community.core.skill_center.path_resolution import (
    canonical_pool_local_path,
)

from .skill_set_context import (
    logger,
)


class SkillSetRuntimeContent:
    def get_symlink_mappings(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        additional_skill_paths: list[str] | None = None,
        desired_skills: list[dict] | None = None,
    ) -> list[SynlinkMappingInfo]:
        """生成技能激活软链配置（支持多能力集激活）

        用于申请设备时传入 ARCA 沙箱，运行时通过 OSS 挂载看到软链。
        支持多个能力集同时激活，合并软链配置并去重。

        Args:
            user_id: 用户 ID
            bolt_id: Bot ID，默认使用 self.bot_id
            additional_skill_paths: 当前请求直接激活、但尚未属于 active
                SkillSet 的 Skill locator。它们与 SkillSet 快照使用同一套
                Engine/Planner 路径规则生成一次完整 publish。

        Returns:
            List[SynlinkMappingInfo]: 软链配置列表（已去重）
        """
        unique_skills = (
            list(desired_skills)
            if desired_skills is not None
            else self.get_active_skills(user_id=user_id, bolt_id=bolt_id)
        )

        # Direct Skill CRUD is intentionally orthogonal to SkillSet membership.
        # The device boundary accepts a complete mapping publish, so the current
        # request must be merged into the active-SkillSet snapshot explicitly;
        # otherwise bindpath can report success while never seeing the requested
        # Skill. Keep the locator as the identity and let the common mapping code
        # below select Legacy/Pool roots for every filesystem engine.
        seen_paths = {
            str(skill.get("git_path", ""))
            for skill in unique_skills
            if skill.get("git_path")
        }
        requested_paths = additional_skill_paths or []
        for skill_path in requested_paths:
            if not isinstance(skill_path, str) or not skill_path.startswith(
                ("git://", "local://")
            ):
                raise ValueError(f"Unsupported direct Skill locator: {skill_path!r}")
            if skill_path in seen_paths:
                continue
            unique_skills.append({"name": "", "git_path": skill_path})
            seen_paths.add(skill_path)

        if not unique_skills:
            return []

        logger.info(
            f"[get_symlink_mappings] 总技能数量（去重后）: {len(unique_skills)}"
        )

        # 4. 构造基础路径（设备容器内的绝对路径）
        # 不同引擎使用不同的容器内技能目录
        ENGINE_SKILLS_DIR_MAP = {
            "claude_code": "/home/admin/.claude/skills",
            "aicoding": "/home/admin/.claude/skills",
            "openclaw": "/home/admin/.openclaw/workspace/skills",
            "moltis": "/home/admin/.moltis/skills",
            "hermes": "/home/admin/.hermes/skills",
            "deepseek_harness": "/home/admin/.dsh/skills",
        }
        # aicoding 引擎的 skills-repo 实际存储位置不同
        ENGINE_SKILLS_REPO_DIR_MAP = {
            "aicoding": "/home/admin/.aicoding/skills-repo",
            "hermes": "/home/admin/.hermes/skills-repo",
        }
        base_skills_dir = Path(
            ENGINE_SKILLS_DIR_MAP.get(
                self.runtime_engine_type,
                "/home/admin/.openclaw/workspace/skills",
            )
        )
        # aicoding 引擎使用独立的 skills-repo 目录
        skills_repo_dir = Path(
            ENGINE_SKILLS_REPO_DIR_MAP.get(
                self.runtime_engine_type, str(base_skills_dir / "skills-repo")
            )
        )
        pool_layout_paths = None
        pool_owner_id = self.user_id or self.entity_id
        if pool_owner_id is not None:
            pool_layout_paths = self._pool_layout_paths(
                str(pool_owner_id),
                str(self.bot_id),
                self.runtime_engine_type,
            )
        if pool_layout_paths is not None:
            active_path, local_path, repo_path = pool_layout_paths
            base_skills_dir = Path(active_path)
            skills_repo_dir = Path(repo_path)

        # singlebox 本机模式: engine adapter 跑在宿主 macOS,容器视图 /home/admin/...
        # 不存在。把容器路径前缀替换成宿主 per-bot workspace 路径
        # (bolt_data/staff_X/<bot>/openclaw/workspace/skills),让 engine 写到 per-bot
        # 物理目录,避免多 bot 共宿主时撞共享根 ~/.openclaw/workspace/skills 的 bug。
        # 线上 Arca 容器内 /home/admin/... 真实存在,不需要换。
        #
        # 注:绕过 path_factory.get_bot_skills_dir 的 LOCAL 短路 (它在 singlebox 模式
        # 下返回共享根,会破坏 per-bot 隔离),直接用 get_bot_engine_dir 算 per-bot path。
        # is_desktop bot 不走这条 — desktop bot 的 mapping 仍按容器视角下发,引擎在
        # VM 内自己解析。

        if self._use_host_skill_mapping_paths():
            from agentclaw.community.core.workspace.path_factory import (
                get_bot_engine_dir,
            )

            per_bot_engine_root = get_bot_engine_dir(
                self.entity_id or "default",
                self.bot_id,
                self.runtime_engine_type,
                self.entity_type or "staff",
            )
            if self.runtime_engine_type == "deepseek_harness":
                per_bot_skills_root = per_bot_engine_root / "skills"
                container_root = Path("/home/admin/.dsh/skills")
            else:
                per_bot_skills_root = per_bot_engine_root / "workspace" / "skills"
                container_root = Path("/home/admin/.openclaw/workspace/skills")
            if base_skills_dir.is_relative_to(container_root):
                base_skills_dir = per_bot_skills_root / base_skills_dir.relative_to(
                    container_root
                )
            if skills_repo_dir.is_relative_to(container_root):
                skills_repo_dir = per_bot_skills_root / skills_repo_dir.relative_to(
                    container_root
                )
            logger.info(
                "[get_symlink_mappings] LOCAL+non-desktop → per-bot paths: "
                "entity=%s bot=%s engine=%s base_skills_dir=%s skills_repo_dir=%s",
                self.entity_id,
                self.bot_id,
                self.runtime_engine_type,
                base_skills_dir,
                skills_repo_dir,
            )

        skills_local_dir = base_skills_dir / "skills-local"
        if pool_layout_paths is not None:
            skills_local_dir = Path(local_path)

        # 5. 解析 git_path 生成 SynlinkMappingInfo 列表（使用绝对路径）
        symlinks = []
        for skill in unique_skills:
            git_path = skill.get("git_path", "")
            skill_name = skill.get("name", "")

            logger.debug(
                f"[get_symlink_mappings] 处理技能: name={skill_name}, git_path={git_path}"
            )

            if git_path.startswith("git://"):
                # Git 技能: 指向 skills-repo
                rel_path = git_path[6:]
                link_name = skill_name or (
                    rel_path.split("/")[-1] if "/" in rel_path else rel_path
                )
                source = str(skills_repo_dir / rel_path)
                target = str(base_skills_dir / link_name)
                symlinks.append(SynlinkMappingInfo(source=source, target=target))

            elif git_path.startswith("local://"):
                # local:// 可能是绝对路径 local:///aidesktop/.../skills-local/skill-name
                # 或者相对名称 local://skill-name
                path_part = git_path[8:]
                # 从路径中提取技能名称（最后一个目录名）
                if path_part.startswith("/"):
                    # 绝对路径格式: /aidesktop/.../skills-local/skill-name
                    link_name = skill_name or path_part.rstrip("/").split("/")[-1]
                    source = (
                        canonical_pool_local_path(path_part, skills_local_dir)
                        if pool_layout_paths is not None
                        else path_part.rstrip("/")
                    )
                else:
                    # 相对名称格式: skill-name
                    source_name = (
                        path_part.split("/")[-1] if "/" in path_part else path_part
                    )
                    # Replacement packages are staged under a temporary
                    # directory (for example ``.foo.replacement-*``), but
                    # their runtime link must retain the logical Skill name.
                    link_name = skill_name or source_name
                    source = str(skills_local_dir / source_name)
                target = str(base_skills_dir / link_name)
                symlinks.append(SynlinkMappingInfo(source=source, target=target))
            elif git_path.startswith("center://"):
                # This legacy file adapter has no Center request contract. A
                # caller must route such a projection through the mapping-v3
                # Engine adapter; silently omitting it would be fail-open.
                raise ValueError(
                    "center skill requires a Center-capable runtime adapter"
                )
            else:
                raise ValueError("unsupported skill source in runtime projection")

        resolved_mappings = symlinks
        if requested_paths:
            # A single runtime link name cannot safely resolve to two corpora.
            # Reject collisions before calling the device boundary; identical
            # mappings are harmless duplicates and collapse in insertion order.
            resolved_mappings = []
            mappings_by_target: dict[str, SynlinkMappingInfo] = {}
            for mapping in symlinks:
                existing = mappings_by_target.get(mapping.target)
                if existing is None:
                    mappings_by_target[mapping.target] = mapping
                    resolved_mappings.append(mapping)
                    continue
                if existing.source != mapping.source:
                    raise ValueError(
                        "Conflicting Skill mappings for runtime target "
                        f"{mapping.target!r}: {existing.source!r} != "
                        f"{mapping.source!r}"
                    )

        logger.info(
            f"[get_symlink_mappings] 生成软链配置完成: symlinks_count={len(resolved_mappings)}"
        )
        for sm in resolved_mappings:
            logger.info(
                f"[get_symlink_mappings] symlink: source={sm.source}, target={sm.target}"
            )

        return resolved_mappings

    def get_set_mcp_servers(
        self,
        skill_set_id: str,
        user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        template_type: Any = None,
        *,
        ext_info: Optional[Mapping[str, Any]] = None,
    ) -> List[dict]:
        """Get all MCP servers in a skill set.

        For default skill sets, merges DEFAULT_MCP_SERVERS_CONFIG and filters out
        user-excluded MCPs from ac_default_skillset_mcp_exclusion.

        Args:
            skill_set_id: Skill set ID
            user_id: User ID for exclusion lookup
            bot_id: Bot ID for exclusion lookup (required for default skill sets)
            engine_type: Engine type for default MCP list. Defaults to self.engine_type.
        """
        skill_set = self.get_skill_set(skill_set_id, user_id)
        if not skill_set:
            return []

        effective_engine = engine_type if engine_type is not None else self.engine_type
        effective_ext_info = (
            ext_info
            if ext_info is not None
            else self._get_default_capabilities_ext_info(effective_engine, bot_id)
        )
        effective_template_type = (
            template_type
            if template_type is not None
            else self._get_default_capabilities_template_type(bot_id)
        )

        # Get associations from DB (contains server_code and name)
        associations = self.skill_set_repo.get_mcp_servers_in_set(skill_set_id)

        # If this is a default skill set, merge with DEFAULT_MCP_SERVERS_CONFIG
        if skill_set.get("is_default"):
            default_codes = get_default_mcp_server_codes(
                effective_engine,
                effective_template_type,
                ext_info=effective_ext_info,
            )
            excluded_codes = set()

            # Use provided bot_id, fallback to self.bot_id, then None
            effective_bot_id = bot_id if bot_id is not None else self.bot_id
            if user_id and effective_bot_id:
                excluded_codes = set(
                    self.skill_set_repo.get_excluded_mcps(
                        user_id, effective_bot_id, int(skill_set_id)
                    )
                )

            associations = [
                association
                for association in associations
                if association.get("server_code") not in excluded_codes
            ]
            db_codes = {a.get("server_code") for a in associations}
            logger.info(
                f"[get_set_mcp_servers] skill_set_id={skill_set_id}, is_default=True, "
                f"engine_type={effective_engine}, db_codes={list(db_codes)}, "
                f"default_codes={default_codes}, excluded_codes={list(excluded_codes)}"
            )

            for code in default_codes:
                if code in excluded_codes:
                    continue  # Skip user-excluded
                if code not in db_codes:
                    # Add default MCP (not in DB)
                    # 分配 mock id，避免前端因 id 为 None 导致 checkbox key 冲突
                    # 用 adler32 保证同一 server_code 的 mock id 稳定，不受列表顺序/排除项影响
                    mock_id = (zlib.adler32(code.encode("utf-8")) % 99999) + 1
                    default_cfg = (
                        get_default_mcp_config(
                            effective_engine,
                            code,
                            effective_template_type,
                            ext_info=effective_ext_info,
                        )
                        or {}
                    )
                    associations.append(
                        {
                            "id": mock_id,
                            "server_code": code,
                            "name": default_cfg.get("name") or code.split(".")[-1],
                            "description": default_cfg.get("description", "默认 MCP"),
                            "icon": default_cfg.get("icon"),
                            "is_default": True,
                        }
                    )

        # Convert to expected format
        result = []
        for assoc in associations:
            result.append(
                {
                    "id": assoc.get("id"),
                    "server_code": assoc.get("server_code"),
                    "name": assoc.get("name"),
                    "description": assoc.get("description"),
                    "icon": assoc.get("icon"),
                    "status": "ONLINE",  # Default status since we don't store full data locally
                    "is_default": assoc.get("is_default", False),
                }
            )
        logger.info(
            f"[get_set_mcp_servers] skill_set_id={skill_set_id}, is_default={skill_set.get('is_default')}, "
            f"engine_type={effective_engine}, result_codes={[r.get('server_code') for r in result]}"
        )
        return result
