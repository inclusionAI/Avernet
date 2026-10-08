"""
File Skill Loader

Worker Profile Ingestion Baseline

从 skills/skill_sets.json 文件加载技能信息。
"""

from __future__ import annotations

import json
import os
from typing import Optional

from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import WorkerProfileWarning
from src.infra.worker_profiles.config.worker_profile_settings import WorkerProfileSettings


class FileSkillLoader:
    """
    文件技能加载器

    从 skills/skill_sets.json 文件加载技能信息。

    规则：
    - 只提取 is_current=true 的 skill set
    - 多个 current → 记录警告，取第一个
    - 没有 current → 记录警告，返回空列表
    """

    def __init__(self, settings: Optional[WorkerProfileSettings] = None):
        """
        初始化加载器

        Args:
            settings: 配置对象
        """
        self._settings = settings or WorkerProfileSettings()

    def load(self, skills_path: str) -> tuple[list[SkillProfile], list[WorkerProfileWarning]]:
        """
        加载技能文件

        Args:
            skills_path: skills 目录路径（包含 skill_sets.json）

        Returns:
            (技能列表, 警告列表)
        """
        skills: list[SkillProfile] = []
        warnings: list[WorkerProfileWarning] = []

        # 构建文件路径
        json_path = os.path.join(skills_path, "skill_sets.json")

        # 检查文件是否存在
        if not os.path.exists(json_path):
            warnings.append(WorkerProfileWarning(
                code="SKILL_FILE_NOT_FOUND",
                message=f"skill_sets.json not found in: {skills_path}",
                source_path=skills_path,
                suggestion="Please add skill_sets.json file with skill definitions",
            ))
            return skills, warnings

        # 读取并解析 JSON
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            warnings.append(WorkerProfileWarning(
                code="SKILL_FILE_PARSE_ERROR",
                message=f"Failed to parse skill_sets.json: {str(e)}",
                source_path=json_path,
                suggestion="Please ensure skill_sets.json is valid JSON",
            ))
            return skills, warnings
        except Exception as e:
            warnings.append(WorkerProfileWarning(
                code="SKILL_FILE_READ_ERROR",
                message=f"Failed to read skill_sets.json: {str(e)}",
                source_path=json_path,
            ))
            return skills, warnings

        # 获取 skill_sets 列表
        skill_sets = data.get("skill_sets", [])
        if not isinstance(skill_sets, list):
            warnings.append(WorkerProfileWarning(
                code="SKILL_SETS_INVALID_FORMAT",
                message="skill_sets must be a list",
                source_path=json_path,
            ))
            return skills, warnings

        # 找到 active skill sets
        active_sets = [s for s in skill_sets if s.get("is_current", False)]

        if len(active_sets) == 0:
            warnings.append(WorkerProfileWarning(
                code="NO_ACTIVE_SKILL_SET",
                message="No active skill set found (no skill_set with is_current=true)",
                source_path=json_path,
                suggestion="Please mark one skill set as is_current=true",
            ))
            return skills, warnings

        if len(active_sets) > 1:
            warnings.append(WorkerProfileWarning(
                code="MULTIPLE_ACTIVE_SKILL_SETS",
                message=f"Multiple active skill sets found: {[s.get('name', 'unnamed') for s in active_sets]}. Using the first one.",
                source_path=json_path,
                suggestion="Only one skill set should have is_current=true",
            ))

        # 只取第一个 active skill set
        active_set = active_sets[0]
        skill_set_name = active_set.get("name", "unnamed")
        skills_data = active_set.get("skills", [])

        # 解析技能
        for skill_data in skills_data:
            if not isinstance(skill_data, dict):
                continue

            name = skill_data.get("name", "")
            skill_id = skill_data.get("skill", "")

            if not name or not skill_id:
                continue

            skill = SkillProfile(
                name=name,
                description=skill_data.get("description"),
                skill_id=skill_id,
                path=skill_data.get("path"),
                skill_set_name=skill_set_name,
                is_active=True,
                metadata={
                    "source_file": json_path,
                },
            )
            skills.append(skill)

        return skills, warnings


__all__ = ["FileSkillLoader"]