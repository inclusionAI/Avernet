"""
API Worker Profile Source

Profile API MVP - 从 API 注册的 Profile 加载 WorkerProfile

将 API 注册的 Profile Content 转换为 WorkerProfile 格式，
使 Retrieval/Recommendation/Fusion 可以使用。
"""

from __future__ import annotations

from typing import Optional

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import (
    WorkerProfile,
    WorkerProfileScanResult,
    ProfileType,
    SourceType,
)
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.domain.services.worker_profile_source import WorkerProfileSource
from src.infra.config.content_embedding_config import ContentEmbeddingConfig


class ApiWorkerProfileSource:
    """
    API Worker Profile 来源

    从 API 注册的 Profile Content Store 加载 Profile，
    转换为 WorkerProfile 格式供检索使用。

    与 FileWorkerProfileSource 并行工作：
    - FileWorkerProfileSource: 从文件系统加载
    - ApiWorkerProfileSource: 从 API 注册加载
    """

    def __init__(self, profile_content_store):
        """
        初始化

        Args:
            profile_content_store: WorkerProfileContentStoreAdapter 实现
        """
        self._store = profile_content_store

    def scan(self) -> WorkerProfileScanResult:
        """
        扫描所有活跃 Profile

        Returns:
            WorkerProfileScanResult
        """
        # 获取所有活跃 Profile
        active_contents = self._store.get_all_active()

        # 转换为 WorkerProfile
        profiles = []
        for content in active_contents:
            profile = self._convert_to_worker_profile(content)
            if profile:
                profiles.append(profile)

        return WorkerProfileScanResult(
            profiles=profiles,
            scan_warnings=[],
            source_roots=["api"],
        )

    def get_profile(
        self, staff_id: str, profile_id: str
    ) -> Optional[WorkerProfile]:
        """
        获取指定 Profile

        Args:
            staff_id: 员工 ID (对应 worker_id)
            profile_id: Profile ID

        Returns:
            WorkerProfile 或 None
        """
        content = self._store.get(staff_id, profile_id)
        if content is None:
            return None

        return self._convert_to_worker_profile(content)

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        """
        获取指定员工的所有 Profile

        Args:
            staff_id: 员工 ID (对应 worker_id)

        Returns:
            WorkerProfile 列表
        """
        result = self._store.list_by_worker(staff_id)

        profiles = []
        for content in result.items:
            profile = self._convert_to_worker_profile(content)
            if profile:
                profiles.append(profile)

        return profiles

    def _convert_to_worker_profile(
        self, content: WorkerProfileContent
    ) -> Optional[WorkerProfile]:
        """
        将 WorkerProfileContent 转换为 WorkerProfile

        Args:
            content: API 注册的 Profile Content

        Returns:
            WorkerProfile 或 None
        """
        if content is None:
            return None

        # 构建 Context Fragments
        # 字段名即 fragment_type（去掉 _md 或 .md 后缀）
        context_fragments = []
        api_source = f"api://{content.worker_id}/{content.profile_id}"

        enabled_fields = ContentEmbeddingConfig.get_embedding_fields()

        # 辅助函数：提取字段名（去掉后缀）
        def extract_field_name(name: str) -> str:
            # 去掉路径
            basename = name.lower().split('/')[-1].split('\\')[-1]
            # 去掉 .md 或 _md 后缀
            if basename.endswith('.md'):
                return basename[:-3]
            if basename.endswith('_md'):
                return basename[:-3]
            return basename

        # 核心字段映射（字段名 -> 内容）
        core_fields = {
            'soul': content.soul_md,
            'agents': content.agents_md,
            'tools': content.tools_md,
            'boot': content.boot_md,
            'heartbeat': content.heartbeat_md,
        }

        # Step 1: 处理核心字段
        for field_name, field_content in core_fields.items():
            if field_content and field_content.strip() and field_name in enabled_fields:
                context_fragments.append(ContextFragment(
                    filename=f"{field_name}.md",
                    content=field_content,
                    source_path=api_source,
                    metadata={"embedding_field": field_name},
                ))

        # Step 2: 处理 contents 字段（默认: memory, profile, capabilities, skill_set）
        for filename, file_content in content.contents.items():
            # 将任意类型转为字符串
            if isinstance(file_content, list):
                content_str = "\n".join(str(item) for item in file_content)
            elif isinstance(file_content, dict):
                content_str = "\n".join(f"{k}: {v}" for k, v in file_content.items())
            else:
                content_str = str(file_content)

            if not content_str.strip():
                continue

            # 提取基础字段名（去掉路径和扩展名）
            basename = filename.lower().split('/')[-1].split('\\')[-1]
            field_name = basename.split('.')[0]

            # 只处理启用了向量化的字段
            if field_name not in enabled_fields:
                continue

            # fragment_type 由 embedding_field 决定
            context_fragments.append(ContextFragment(
                filename=filename,
                content=content_str,
                source_path=f"{api_source}/{filename}",
                metadata={
                    "original_name": filename,
                    "embedding_field": field_name,
                },
            ))

        # 构建 Skills
        # API 注册的技能使用简化字段
        active_skills = []
        for idx, skill in enumerate(content.skill_sets):
            active_skills.append(SkillProfile(
                name=skill.name,
                description=skill.description or "",
                skill_id=f"api_skill_{content.worker_id}_{idx}",
                path=None,
                skill_set_name="api_skills",
                is_active=True,
                metadata=skill.metadata,
            ))

        # 将 skill_sets 作为单独的 context_fragment（如果启用了向量化）
        if "skill_set" in enabled_fields and content.skill_sets:
            skill_lines = []
            for s in content.skill_sets:
                skill_text = s.name
                if s.description:
                    skill_text += f": {s.description}"
                if s.content:
                    skill_text += f"\n{s.content}"
                skill_lines.append(skill_text)

            if skill_lines:
                skill_content = "\n\n".join(skill_lines)
                context_fragments.append(ContextFragment(
                    filename="skill_set.md",
                    content=skill_content,
                    source_path=api_source,
                    metadata={
                        "embedding_field": "skill_set",
                        "original_name": "skill_set",
                    },
                ))

        # 创建 WorkerProfile
        profile = WorkerProfile(
            staff_id=content.worker_id,
            profile_id=content.profile_id,
            profile_type=ProfileType.BOT,  # API 注册的通常是 Bot
            source_type=SourceType.API,
            source_root="api",
            context_fragments=context_fragments,
            active_skills=active_skills,
        )

        # 生成可检索文本
        profile.generate_searchable_text()

        # 追加 metadata 中的信息
        if content.metadata:
            extra_text = content.generate_searchable_text()
            profile.searchable_text = f"{profile.searchable_text} {extra_text}"

        return profile


__all__ = ["ApiWorkerProfileSource"]