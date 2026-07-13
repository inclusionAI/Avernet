"""
Tests for File Skill Loader

Worker Profile Ingestion Baseline

测试范围：
- FileSkillLoader: 技能文件加载器
- skill_sets.json 读取
- active skill set 解析
- 多个/无 active skill set 警告
"""

from __future__ import annotations

import json
import os
import tempfile
import pytest


class TestFileSkillLoader:
    """测试 FileSkillLoader"""

    def test_load_skills_success(self):
        """测试成功加载技能"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        skill_sets_data = {
            "skill_sets": [
                {
                    "name": "default_skills",
                    "is_current": True,
                    "skills": [
                        {
                            "name": "web_search",
                            "description": "Search the web",
                            "skill": "search_web_v1",
                            "path": "/skills/search/web",
                        },
                        {
                            "name": "code_review",
                            "description": "Review code",
                            "skill": "review_v1",
                            "path": "/skills/review",
                        },
                    ],
                },
                {
                    "name": "old_skills",
                    "is_current": False,
                    "skills": [
                        {
                            "name": "deprecated_skill",
                            "skill": "old_v1",
                        },
                    ],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                json.dump(skill_sets_data, f)

            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            # 只加载 active skill set
            assert len(skills) == 2
            assert all(s.is_active for s in skills)

            # 验证技能内容
            skill_names = [s.name for s in skills]
            assert "web_search" in skill_names
            assert "code_review" in skill_names
            assert "deprecated_skill" not in skill_names

            # 无警告
            assert len(warnings) == 0

    def test_load_skills_no_file(self):
        """测试文件不存在"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(tmpdir)

            assert skills == []
            assert len(warnings) == 1
            assert "not found" in warnings[0].message.lower()

    def test_load_skills_invalid_json(self):
        """测试无效 JSON 文件"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                f.write("{ invalid json }")

            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            assert skills == []
            assert len(warnings) == 1
            assert "parse" in warnings[0].message.lower() or "json" in warnings[0].message.lower()

    def test_load_skills_no_active_set(self):
        """测试没有激活的技能组"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        skill_sets_data = {
            "skill_sets": [
                {
                    "name": "old_skills",
                    "is_current": False,
                    "skills": [{"name": "old_skill", "skill": "old_v1"}],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                json.dump(skill_sets_data, f)

            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            assert skills == []
            assert len(warnings) == 1
            assert "no active" in warnings[0].message.lower()

    def test_load_skills_multiple_active_sets(self):
        """测试多个激活的技能组"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        skill_sets_data = {
            "skill_sets": [
                {
                    "name": "skills_set_1",
                    "is_current": True,
                    "skills": [{"name": "skill_1", "skill": "v1"}],
                },
                {
                    "name": "skills_set_2",
                    "is_current": True,
                    "skills": [{"name": "skill_2", "skill": "v2"}],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                json.dump(skill_sets_data, f)

            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            # 取第一个 active skill set
            assert len(skills) == 1
            assert skills[0].name == "skill_1"

            # 有警告
            assert len(warnings) == 1
            assert "multiple" in warnings[0].message.lower()

    def test_load_skills_empty_skill_set(self):
        """测试空的技能组"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        skill_sets_data = {
            "skill_sets": [
                {
                    "name": "empty_skills",
                    "is_current": True,
                    "skills": [],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                json.dump(skill_sets_data, f)

            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            # 空技能组
            assert skills == []
            # 没有 active skill set 的警告（因为 active set 存在但为空）
            # 或者根据实现可能有警告
            assert len(warnings) == 0  # active set 存在，只是没有 skills

    def test_load_skills_preserves_skill_set_name(self):
        """测试保留技能组名称"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        skill_sets_data = {
            "skill_sets": [
                {
                    "name": "my_custom_skill_set",
                    "is_current": True,
                    "skills": [
                        {
                            "name": "test_skill",
                            "skill": "test_v1",
                        },
                    ],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                json.dump(skill_sets_data, f)

            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            assert len(skills) == 1
            assert skills[0].skill_set_name == "my_custom_skill_set"

    def test_load_skills_optional_fields(self):
        """测试可选字段处理"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        skill_sets_data = {
            "skill_sets": [
                {
                    "name": "minimal_skills",
                    "is_current": True,
                    "skills": [
                        {
                            "name": "minimal_skill",
                            "skill": "minimal_v1",
                            # 没有 description 和 path
                        },
                    ],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                json.dump(skill_sets_data, f)

            settings = WorkerProfileSettings()
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            assert len(skills) == 1
            assert skills[0].name == "minimal_skill"
            assert skills[0].skill_id == "minimal_v1"
            assert skills[0].description is None
            assert skills[0].path is None


class TestFileSkillLoaderAllowNoActive:
    """测试 allow_no_active_skillset 配置"""

    def test_allow_no_active_skillset_true(self):
        """测试允许没有激活技能组"""
        from src.infra.worker_profiles.loaders.file_skill_loader import (
            FileSkillLoader,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        skill_sets_data = {
            "skill_sets": [
                {
                    "name": "inactive_skills",
                    "is_current": False,
                    "skills": [{"name": "inactive_skill", "skill": "v1"}],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            skills_path = os.path.join(tmpdir, "skills")
            os.makedirs(skills_path)

            json_path = os.path.join(skills_path, "skill_sets.json")
            with open(json_path, "w") as f:
                json.dump(skill_sets_data, f)

            settings = WorkerProfileSettings(allow_no_active_skillset=True)
            loader = FileSkillLoader(settings)
            skills, warnings = loader.load(skills_path)

            # 允许没有 active skill set，返回空列表但有警告
            assert skills == []
            # 仍然有警告，但不应该阻断流程
            assert len(warnings) == 1