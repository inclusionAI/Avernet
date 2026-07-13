"""
API Profile Source

Profile API MVP Phase 2 - 从 bcsfuse_worker_profile_contents 表读取并转换为 WorkerProfile

解决Sparse Context问题：
- 将 soul_md、agents_md、tools_md 转换为 context_fragments
- 将 skill_sets 转换为 active_skills
- 生成 searchable_text

关键修复：
- 解决 API 注册的 Profile 只有 soul_md，缺少 context_fragments 和 active_skills 的问题
- 使 CompositeWorkerProfileSource 能够正确合并 API Profile 内容
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from src.domain.models.context_fragment import ContextFragment, ContextKind
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import (
    ProfileType,
    SourceType,
    WorkerProfile,
    WorkerProfileScanResult,
    WorkerProfileWarning,
)
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.domain.services.worker_profile_source import WorkerProfileSource
from src.infra.config.content_embedding_config import ContentEmbeddingConfig

logger = logging.getLogger(__name__)


class APIProfileSource(WorkerProfileSource):
    """
    API Profile Source

    从 bcsfuse_worker_profile_contents 表读取 Profile 内容，并转换为 WorkerProfile。

    核心功能：
    1. 读取 API 上传的 soul_md、agents_md、tools_md、skill_sets 等内容
    2. 将这些内容转换为 context_fragments 和 active_skills
    3. 生成 searchable_text 用于检索

    解决问题：
    - Sparse Context: 自动从 soul_md 提取 context_fragments
    - 技能提取: 从 skill_sets 提取 active_skills
    """

    def __init__(self, content_store=None):
        """
        初始化 API Profile Source

        Args:
            content_store: Profile Content Store 实例（可选，如果不提供会自动创建）
        """
        self._content_store = content_store
        self._scan_result: Optional[WorkerProfileScanResult] = None

    def _parse_contents_map(self, raw_contents):
        """
        Normalize contents field into dict.

        Supports:
        - dict: returned as-is
        - JSON string: parsed into dict
        - None / invalid values: returns {}

        Args:
            raw_contents: Raw contents field (dict, JSON string, or None)

        Returns:
            dict: Normalized contents map
        """
        if not raw_contents:
            return {}

        if isinstance(raw_contents, dict):
            return raw_contents

        if isinstance(raw_contents, str):
            try:
                parsed = json.loads(raw_contents)
                if isinstance(parsed, dict):
                    return parsed
                logger.warning(
                    f"[APIProfileSource] Parsed contents is not dict: type={type(parsed).__name__}"
                )
                return {}
            except json.JSONDecodeError as e:
                logger.warning(
                    f"[APIProfileSource] Failed to parse contents JSON string: {e}"
                )
                return {}

        logger.warning(
            f"[APIProfileSource] Unknown contents type: type={type(raw_contents).__name__}"
        )
        return {}

    def _extract_content_text_from_contents(self, contents: dict) -> tuple[str | None, str | None]:
        """
        Extract searchable primary content from contents map.

        Priority:
        1. soul_md / SOUL.md / soul.md / SOUL
        2. agents_md / AGENTS.md
        3. tools_md / TOOLS.md
        4. boot_md / BOOT.md
        5. common custom markdown fields: think.md / experience.md / decision.md
        6. merge all non-empty string fields

        Args:
            contents: Normalized contents dict

        Returns:
            tuple: (content_text, content_source) where content_source indicates extraction method
        """
        if not contents:
            return None, None

        # Priority 1: soul_md variants
        for key in ["soul_md", "SOUL.md", "soul.md", "SOUL"]:
            if key in contents and contents[key] and isinstance(contents[key], str):
                logger.debug(
                    f"[APIProfileSource] Extracted soul_md from contents: key={key}, len={len(contents[key])}"
                )
                return contents[key], f"contents.{key}"

        # Priority 2: agents_md variants
        for key in ["agents_md", "AGENTS.md"]:
            if key in contents and contents[key] and isinstance(contents[key], str):
                return contents[key], f"contents.{key}"

        # Priority 3: tools_md variants
        for key in ["tools_md", "TOOLS.md"]:
            if key in contents and contents[key] and isinstance(contents[key], str):
                return contents[key], f"contents.{key}"

        # Priority 4: boot_md variants
        for key in ["boot_md", "BOOT.md"]:
            if key in contents and contents[key] and isinstance(contents[key], str):
                return contents[key], f"contents.{key}"

        # Priority 5: common custom markdown fields
        for key in ["think.md", "experience.md", "decision.md", "AGENTS.md", "TOOLS.md"]:
            if key in contents and contents[key] and isinstance(contents[key], str):
                return contents[key], f"contents.{key}"

        # Priority 6: merge all non-empty string fields
        text_parts = []
        for key, value in contents.items():
            if isinstance(value, str) and value.strip():
                text_parts.append(f"# {key}\n\n{value}")

        if text_parts:
            merged = "\n\n".join(text_parts)
            return merged, "contents.merged"

        return None, None

    def _normalize_profile_record(self, record, worker_id: str = None, profile_id: str = None) -> WorkerProfileContent:
        """
        Normalize profile record from different storage formats to WorkerProfileContent.

        Phase C2 Fix: Support OSS Business Routes dict format

        Supports:
        - Case A: WorkerProfileContent object (already normalized)
        - Case B: OSS dict format {"content": "...", "metadata": {...}}
        - Case C: Section dict format {"SOUL.md": "...", "AGENTS.md": "...", "metadata": {...}}
        - Case D: Full dict format {"soul_md": "...", "agents_md": "...", "metadata": {...}}

        Args:
            record: Profile record (WorkerProfileContent or dict)
            worker_id: Worker ID (used as fallback for dict records)
            profile_id: Profile ID (used as fallback for dict records)

        Returns:
            WorkerProfileContent instance

        Raises:
            ValueError: If content_text is empty or record format is unknown
        """
        logger.debug(
            f"[APIProfileSource] Normalizing profile record: "
            f"type={type(record).__name__}, "
            f"worker_id={worker_id}, profile_id={profile_id}"
        )

        # Case A: Already WorkerProfileContent object
        if isinstance(record, WorkerProfileContent):
            logger.debug(
                f"[APIProfileSource] Record is already WorkerProfileContent: "
                f"soul_md_len={len(record.soul_md) if record.soul_md else 0}, "
                f"metadata_keys={list(record.metadata.keys()) if record.metadata else []}"
            )
            return record

        # Case B/C/D: Dict format - normalize
        if not isinstance(record, dict):
            raise ValueError(
                f"[APIProfileSource] Unknown profile record type: {type(record).__name__}. "
                f"Expected WorkerProfileContent or dict, got {type(record).__name__}"
            )

        # Dict normalization
        logger.debug(
            f"[APIProfileSource] Normalizing dict profile: "
            f"keys={list(record.keys())}, "
            f"has_content={'content' in record}, has_soul_md={'soul_md' in record}, "
            f"has_SOUL={'SOUL.md' in record}"
        )

        # Extract worker_id and profile_id
        final_worker_id = record.get("worker_id") or worker_id
        final_profile_id = record.get("profile_id") or profile_id or "default"

        if not final_worker_id:
            raise ValueError(
                f"[APIProfileSource] Cannot normalize profile: missing worker_id. "
                f"record_keys={list(record.keys())}, worker_id={worker_id}"
            )

        # Extract content_text (primary content)
        content_text = None
        content_source = None

        # Try different content field names (priority order)
        if "content" in record and record["content"]:
            content_text = record["content"]
            content_source = "content"
        elif "soul_md" in record and record["soul_md"]:
            content_text = record["soul_md"]
            content_source = "soul_md"
        elif "SOUL.md" in record and record["SOUL.md"]:
            content_text = record["SOUL.md"]
            content_source = "SOUL.md"
        elif "profile" in record and record["profile"]:
            content_text = record["profile"]
            content_source = "profile"

        # If no single content field, try to merge from sections
        if not content_text and "sections" in record and isinstance(record["sections"], dict):
            sections = record["sections"]
            content_parts = []
            for section_name in ["SOUL.md", "AGENTS.md", "TOOLS.md", "BOOT.md"]:
                if section_name in sections and sections[section_name]:
                    content_parts.append(f"# {section_name.replace('.md', '')}\n\n{sections[section_name]}")
            if content_parts:
                content_text = "\n\n".join(content_parts)
                content_source = "sections"

        # Phase 3.4.1F Fix: If still no content_text, extract from contents map
        if not content_text or not content_text.strip():
            contents = self._parse_contents_map(record.get("contents"))
            if contents:
                content_text, content_source = self._extract_content_text_from_contents(contents)
                if content_text:
                    logger.debug(
                        f"[APIProfileSource] Extracted content from contents map: "
                        f"source={content_source}, len={len(content_text)}, "
                        f"contents_keys={list(contents.keys())}"
                    )

        # Validate content_text
        if not content_text or not content_text.strip():
            raise ValueError(
                f"[APIProfileSource] Cannot normalize profile: content_text is empty. "
                f"record_keys={list(record.keys())}, content_source={content_source}, "
                f"worker_id={final_worker_id}, profile_id={final_profile_id}"
            )

        # Extract metadata
        metadata = record.get("metadata") or {}

        # Phase 3.4.1F Fix: Parse contents map for fallback extraction
        contents = self._parse_contents_map(record.get("contents"))

        # Extract soul_md (primary soul content)
        # Priority: top-level soul_md > top-level SOUL.md > contents.soul_md > contents.SOUL.md > content_text
        soul_md = (
            record.get("soul_md") or
            record.get("SOUL.md") or
            contents.get("soul_md") or
            contents.get("SOUL.md") or
            contents.get("soul.md") or
            contents.get("SOUL") or
            content_text
        )

        # Extract agents_md
        # Priority: top-level agents_md > top-level AGENTS.md > contents.agents_md > contents.AGENTS.md
        agents_md = (
            record.get("agents_md") or
            record.get("AGENTS.md") or
            contents.get("agents_md") or
            contents.get("AGENTS.md") or
            ""
        )

        # Extract tools_md
        # Priority: top-level tools_md > top-level TOOLS.md > contents.tools_md > contents.TOOLS.md
        tools_md = (
            record.get("tools_md") or
            record.get("TOOLS.md") or
            contents.get("tools_md") or
            contents.get("TOOLS.md") or
            ""
        )

        # Extract boot_md
        # Priority: top-level boot_md > top-level BOOT.md > contents.boot_md > contents.BOOT.md
        boot_md = (
            record.get("boot_md") or
            record.get("BOOT.md") or
            contents.get("boot_md") or
            contents.get("BOOT.md") or
            ""
        )

        # Extract skill_sets
        skill_sets = []
        if "skill_sets" in record and record["skill_sets"]:
            from src.domain.models.worker_profile_content import SkillSet
            for skill_data in record["skill_sets"]:
                if isinstance(skill_data, SkillSet):
                    skill_sets.append(skill_data)
                elif isinstance(skill_data, dict):
                    skill_sets.append(SkillSet(**skill_data))

        # Create WorkerProfileContent
        # Phase 3.4.1F Fix: Use parsed contents dict, not raw record.get("contents")
        normalized = WorkerProfileContent(
            worker_id=final_worker_id,
            profile_id=final_profile_id,
            soul_md=soul_md,
            agents_md=agents_md,
            tools_md=tools_md,
            boot_md=boot_md,
            metadata=metadata,
            skill_sets=skill_sets,
            contents=contents,  # Use parsed dict, not raw value
        )

        logger.info(
            f"[APIProfileSource] Profile normalized successfully: "
            f"profile_key={normalized.profile_key}, "
            f"content_source={content_source}, "
            f"soul_md_len={len(soul_md) if soul_md else 0}, "
            f"metadata_keys={list(metadata.keys())}, "
            f"profile_format={'oss_dict_content' if content_source == 'content' else 'object_worker_profile_content'}"
        )

        return normalized

    def _get_content_store(self):
        """获取或创建 Content Store - SQLite only for open-core"""
        if self._content_store is None:
            # SQLite 模式：本地存储（open-core only）
            from src.infra.adapters.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
            from src.infra.config.worker_registry_settings import WorkerRegistrySettings

            settings = WorkerRegistrySettings()
            db_path = settings.get_effective_db_path()

            self._content_store = SQLiteWorkerProfileContentStore(db_path)
            logger.info(f"[Open-Core] APIProfileSource using SQLite store, path={db_path}")

        return self._content_store

    def scan(self) -> WorkerProfileScanResult:
        """
        扫描所有 API Profile

        Phase G6H Fix: Remove scan cache to match root_original ApiWorkerProfileSource behavior.
        Root original performs direct DB query every time without caching.

        This fixes the stale cache issue where newly created workers are not visible
        in subsequent scans within the same process lifetime.

        Returns:
            WorkerProfileScanResult: 包含所有 API Profile 的扫描结果
        """
        # Phase G6H Fix: Direct DB query every time, no caching
        # This matches root_original ApiWorkerProfileSource behavior
        store = self._get_content_store()
        profiles = []
        scan_warnings = []  # 使用正确的字段名

        try:
            # 获取所有活跃的 Profile
            all_records = store.get_all_active()

            for record in all_records:
                try:
                    # Phase C3 Fix: Normalize record to WorkerProfileContent
                    # Extract worker_id and profile_id from record
                    worker_id = None
                    profile_id = None
                    if isinstance(record, dict):
                        worker_id = record.get("worker_id")
                        profile_id = record.get("profile_id")
                    elif hasattr(record, 'worker_id'):
                        worker_id = record.worker_id
                        profile_id = record.profile_id

                    if not worker_id:
                        logger.warning(f"[APIProfileSource] Skipping record without worker_id: {type(record).__name__}")
                        continue

                    content = self._normalize_profile_record(record, worker_id, profile_id)
                    profile = self._convert_to_worker_profile(content)
                    if profile:
                        profiles.append(profile)
                        logger.debug(
                            f"[APIProfileSource] Converted profile: {profile.profile_key}, "
                            f"fragments={len(profile.context_fragments or [])}, "
                            f"skills={len(profile.active_skills or [])}"
                        )
                except ValueError as e:
                    # Normalization error
                    worker_id_str = "unknown"
                    profile_id_str = "unknown"
                    if isinstance(record, dict):
                        worker_id_str = record.get("worker_id", "unknown")
                        profile_id_str = record.get("profile_id", "unknown")
                    elif hasattr(record, 'worker_id'):
                        worker_id_str = record.worker_id
                        profile_id_str = record.profile_id

                    scan_warnings.append(WorkerProfileWarning(
                        code="PROFILE_NORMALIZATION_ERROR",
                        message=f"Failed to normalize profile {worker_id_str}:{profile_id_str}: {e}",
                        source_path=None,
                        suggestion="Check profile content format - must have non-empty content field"
                    ))
                    logger.warning(
                        f"[APIProfileSource] Failed to normalize profile {worker_id_str}:{profile_id_str}: {e}"
                    )
                except Exception as e:
                    worker_id_str = "unknown"
                    profile_id_str = "unknown"
                    if isinstance(record, dict):
                        worker_id_str = record.get("worker_id", "unknown")
                        profile_id_str = record.get("profile_id", "unknown")
                    elif hasattr(record, 'worker_id'):
                        worker_id_str = record.worker_id
                        profile_id_str = record.profile_id

                    scan_warnings.append(WorkerProfileWarning(
                        code="PROFILE_CONVERSION_ERROR",
                        message=f"Failed to convert profile {worker_id_str}:{profile_id_str}: {e}",
                        source_path=None,
                        suggestion="Check profile content format"
                    ))
                    logger.warning(
                        f"[APIProfileSource] Failed to convert profile {worker_id_str}:{profile_id_str}: {e}"
                    )

            # Phase G6H Fix: Do NOT cache the result, return directly
            result = WorkerProfileScanResult(
                profiles=profiles,
                scan_warnings=scan_warnings  # 使用正确的字段名
            )

            logger.info(
                f"[APIProfileSource] Scan complete: {len(profiles)} profiles, {len(scan_warnings)} warnings"
            )

            return result

        except Exception as e:
            logger.error(f"[APIProfileSource] Scan failed: {e}")
            # Phase G6H Fix: Do NOT cache the error result, return directly
            return WorkerProfileScanResult(
                profiles=[],
                scan_warnings=[WorkerProfileWarning(  # 使用正确的字段名
                    code="SCAN_ERROR",
                    message=str(e),
                    source_path=None,
                    suggestion="Check database connection"
                )]
            )

    def get_profile(self, staff_id: str, profile_id: str) -> Optional[WorkerProfile]:
        """
        获取指定的 Profile

        Args:
            staff_id: 员工 ID（对应 worker_id）
            profile_id: Profile ID

        Returns:
            WorkerProfile 或 None
        """
        store = self._get_content_store()

        try:
            # 从 content store 获取（使用 get 方法，不是 get_profile）
            record = store.get(staff_id, profile_id)

            if record is None:
                logger.debug(f"[APIProfileSource] Profile not found: {staff_id}:{profile_id}")
                return None

            # Phase C3 Fix: Normalize record to WorkerProfileContent
            # Phase D2: Track profile format for diagnostics
            profile_format = None
            try:
                # Determine format before normalization
                if isinstance(record, WorkerProfileContent):
                    profile_format = "object_worker_profile_content"
                elif isinstance(record, dict):
                    if "content" in record:
                        profile_format = "oss_dict_content"
                    elif "SOUL.md" in record or "AGENTS.md" in record:
                        profile_format = "section_dict"
                    elif "soul_md" in record or "agents_md" in record:
                        profile_format = "full_dict"
                    else:
                        profile_format = "unknown_dict"
                else:
                    profile_format = "unknown"

                content = self._normalize_profile_record(record, staff_id, profile_id)
            except ValueError as e:
                logger.error(
                    f"[APIProfileSource] Failed to normalize profile {staff_id}:{profile_id}: {e}"
                )
                return None

            # 转换为 WorkerProfile，传递 profile_format
            profile = self._convert_to_worker_profile(content, profile_format)

            if profile:
                logger.info(
                    f"[APIProfileSource] Retrieved profile: {staff_id}:{profile_id}, "
                    f"fragments={len(profile.context_fragments or [])}, "
                    f"skills={len(profile.active_skills or [])}, "
                    f"profile_format={profile_format}"
                )

            return profile

        except ValueError as e:
            # Re-raise normalization errors with explicit error logging
            logger.error(f"[APIProfileSource] Profile normalization error {staff_id}:{profile_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"[APIProfileSource] Failed to get profile {staff_id}:{profile_id}: {e}")
            return None

    def _convert_to_worker_profile(self, content, profile_format: str = None) -> Optional[WorkerProfile]:
        """
        将 WorkerProfileContent 转换为 WorkerProfile

        核心转换逻辑：
        1. soul_md → context_fragments (experience)
        2. agents_md → context_fragments (workflow)
        3. tools_md → context_fragments (tool)
        4. skill_sets → active_skills
        5. 生成 searchable_text

        Phase D2: 添加 metadata 诊断信息

        Args:
            content: WorkerProfileContent 实例
            profile_format: profile 格式类型（用于诊断）

        Returns:
            WorkerProfile 实例
        """
        if content is None:
            return None

        # 提取 context_fragments
        fragments = self._extract_fragments(content)

        # 提取 active_skills
        skills = self._extract_skills(content)

        # 创建 WorkerProfile（只使用模型支持的字段）
        profile = WorkerProfile(
            staff_id=content.worker_id,
            profile_id=content.profile_id,
            profile_type=ProfileType.BOT,  # API 注册的都是 bot
            source_type=SourceType.API,
            source_root="api",
            context_fragments=fragments,
            active_skills=skills,
        )

        # 生成 searchable_text
        profile.generate_searchable_text()

        # 补充额外的 searchable_text
        extra_text_parts = []

        if content.soul_md:
            # 从 soul_md 中提取关键词
            extra_text_parts.append(self._extract_keywords(content.soul_md))

        if content.agents_md:
            extra_text_parts.append(self._extract_keywords(content.agents_md))

        if extra_text_parts:
            profile.searchable_text = f"{profile.searchable_text} {' '.join(extra_text_parts)}"

        # 追加 content.metadata 中的额外信息（兼容旧版逻辑）
        if content.metadata:
            extra_text = content.generate_searchable_text()
            if extra_text:
                profile.searchable_text = f"{profile.searchable_text} {extra_text}"

        # Phase D2: Set metadata for diagnostics
        # Phase 2.3.1 Fix: Preserve original content.metadata (including test_id, business_regression, domain, etc.)
        diagnostic_metadata = {
            "profile_format": profile_format or "object_worker_profile_content",
            "profile_format_conversion_success": True,
            "normalized_content_length": len(content.soul_md) if content.soul_md else 0,
            "normalized_metadata_keys": list(content.metadata.keys()) if content.metadata else [],
            "context_fragments_count": len(fragments),
            "active_skills_count": len(skills),
            "searchable_text_length": len(profile.searchable_text) if profile.searchable_text else 0,
        }

        # Merge: business metadata takes precedence, then diagnostic metadata
        profile.metadata = {
            **diagnostic_metadata,
            **(content.metadata or {}),
        }

        logger.debug(
            f"[APIProfileSource] Converted: {content.worker_id}:{content.profile_id}, "
            f"fragments={len(fragments)}, skills={len(skills)}, "
            f"searchable_text_len={len(profile.searchable_text or '')}, "
            f"profile_format={profile.metadata['profile_format']}"
        )

        return profile

    def _extract_fragments(self, content) -> list[ContextFragment]:
        """
        从 Profile 内容中提取上下文片段

        转换规则：
        - soul_md → experience 片段 (核心身份和专业能力)
        - agents_md → workflow 片段 (工作流程和配置)
        - tools_md → tool 片段 (工具使用说明)

        Args:
            content: WorkerProfileContent 实例

        Returns:
            ContextFragment 列表
        """
        fragments = []

        # 1. 从 soul_md 提取核心身份和专业能力
        if content.soul_md and content.soul_md.strip():
            # 解析 soul_md 的各个章节
            sections = self._parse_markdown_sections(content.soul_md)

            # 为每个章节创建一个 fragment
            for section_title, section_content in sections.items():
                if section_content.strip():
                    fragment = ContextFragment(
                        kind=self._determine_fragment_kind(section_title),
                        filename="SOUL.md",
                        content=section_content.strip(),
                        source_path="api://soul_md",
                        metadata={
                            "section": section_title,
                            "original_file": "SOUL.md"
                        }
                    )
                    fragments.append(fragment)

            # 如果没有解析出章节，使用整个 soul_md
            if not fragments:
                fragment = ContextFragment(
                    kind=ContextKind.SOUL,
                    filename="SOUL.md",
                    content=content.soul_md.strip(),
                    source_path="api://soul_md",
                    metadata={
                        "section": "full",
                        "original_file": "SOUL.md"
                    }
                )
                fragments.append(fragment)

        # 2. 从 agents_md 提取工作流程
        if content.agents_md and content.agents_md.strip():
            fragment = ContextFragment(
                kind=ContextKind.AGENT,
                filename="AGENTS.md",
                content=content.agents_md.strip(),
                source_path="api://agents_md",
                metadata={
                    "section": "workflow",
                    "original_file": "AGENTS.md"
                }
            )
            fragments.append(fragment)

        # 3. 从 tools_md 提取工具配置
        if content.tools_md and content.tools_md.strip():
            fragment = ContextFragment(
                kind=ContextKind.TOOLS,
                filename="TOOLS.md",
                content=content.tools_md.strip(),
                source_path="api://tools_md",
                metadata={
                    "section": "tools",
                    "original_file": "TOOLS.md"
                }
            )
            fragments.append(fragment)

        # 4. 从 boot_md 提取启动配置
        if hasattr(content, 'boot_md') and content.boot_md and content.boot_md.strip():
            fragment = ContextFragment(
                kind=ContextKind.BOOT,
                filename="BOOT.md",
                content=content.boot_md.strip(),
                source_path="api://boot_md",
                metadata={
                    "section": "boot",
                    "original_file": "BOOT.md"
                }
            )
            fragments.append(fragment)

        # 5. 从 contents Map 提取扩展内容（只处理启用了向量化的字段）
        enabled_fields = ContentEmbeddingConfig.get_embedding_fields()
        if hasattr(content, 'contents') and content.contents:
            for name, md_content in content.contents.items():
                # 处理非字符串类型（如 list, dict）
                if isinstance(md_content, list):
                    # list 类型：转换为逗号分隔的字符串
                    content_str = ", ".join(str(item) for item in md_content[:5])  # 最多取5个元素
                    if not content_str:
                        continue
                elif isinstance(md_content, dict):
                    # dict 类型：转换为 key=value 格式
                    content_str = ", ".join(f"{k}={v}" for k, v in list(md_content.items())[:3])
                    if not content_str:
                        continue
                elif isinstance(md_content, str):
                    # 字符串类型：正常处理
                    if not md_content or not md_content.strip():
                        continue
                    content_str = md_content.strip()
                else:
                    # 其他类型：跳过
                    continue

                # 跳过核心字段（已在上方处理）
                if name.lower() in WorkerProfileContent.CORE_CONTENT_NAMES:
                    continue

                # 提取基础字段名（去掉路径和扩展名）
                basename = name.lower().split('/')[-1].split('\\')[-1]
                field_name = basename.split('.')[0]

                # 只处理启用了向量化的字段
                if field_name not in enabled_fields:
                    continue

                fragment = ContextFragment(
                    filename=name,
                    content=content_str,
                    source_path=f"api://contents/{name}",
                    metadata={
                        "section": "custom",
                        "original_file": name,
                        "embedding_field": field_name,
                    }
                )
                fragments.append(fragment)

        # 6. 将 skill_sets 作为单独的 context_fragment（如果启用了向量化）
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
                fragments.append(ContextFragment(
                    filename="skill_set.md",
                    content=skill_content,
                    source_path=f"api://{content.worker_id}/{content.profile_id}/skill_set.md",
                    metadata={
                        "embedding_field": "skill_set",
                        "original_file": "skill_set",
                        "section": "skills",
                    },
                ))

        logger.debug(
            f"[APIProfileSource] Extracted {len(fragments)} fragments: "
            f"soul_md={bool(content.soul_md)}, agents_md={bool(content.agents_md)}, "
            f"tools_md={bool(content.tools_md)}, contents={len(getattr(content, 'contents', {}) or {})}"
        )

        return fragments

    def _extract_skills(self, content) -> list[SkillProfile]:
        """
        从 Profile 内容中提取技能

        转换规则：
        - skill_sets 列表 → SkillProfile 列表
        - 为每个技能生成合成 skill_id 和 skill_set_name

        Args:
            content: WorkerProfileContent 实例

        Returns:
            SkillProfile 列表
        """
        skills = []

        if not content.skill_sets:
            return skills

        for idx, skill_data in enumerate(content.skill_sets):
            try:
                # skill_data 可能是 SkillSet 对象或字典
                if hasattr(skill_data, 'name'):
                    skill_name = skill_data.name
                    skill_description = getattr(skill_data, 'description', None)
                    skill_content = getattr(skill_data, 'content', None)
                    skill_metadata = getattr(skill_data, 'metadata', {})
                elif isinstance(skill_data, dict):
                    skill_name = skill_data.get('name', f'unknown_{idx}')
                    skill_description = skill_data.get('description')
                    skill_content = skill_data.get('content')
                    skill_metadata = skill_data.get('metadata', {})
                else:
                    logger.warning(f"[APIProfileSource] Unknown skill_data type: {type(skill_data)}")
                    continue

                # 创建 SkillProfile（为 API 来源的技能生成合成 ID）
                skill = SkillProfile(
                    name=skill_name,
                    description=skill_description,
                    skill_id=f"api_skill_{content.worker_id}_{idx}_{skill_name.lower().replace(' ', '_')}",
                    path=skill_content,
                    skill_set_name=f"api_skills_{content.worker_id}",
                    is_active=True,
                    metadata={
                        **skill_metadata,
                        "source": "api",
                        "worker_id": content.worker_id,
                    },
                )

                skills.append(skill)

            except Exception as e:
                logger.warning(f"[APIProfileSource] Failed to extract skill: {e}")
                continue

        logger.debug(f"[APIProfileSource] Extracted {len(skills)} skills from skill_sets")

        return skills

    def _parse_markdown_sections(self, markdown_text: str) -> dict[str, str]:
        """
        解析 Markdown 文本的章节

        Args:
            markdown_text: Markdown 文本

        Returns:
            Dict[section_title, section_content]
        """
        sections = {}

        # 匹配 ## 标题
        pattern = r'^##\s+(.+)$'
        lines = markdown_text.split('\n')

        current_section = None
        current_content = []

        for line in lines:
            match = re.match(pattern, line)
            if match:
                # 保存前一个章节
                if current_section and current_content:
                    sections[current_section] = '\n'.join(current_content).strip()

                # 开始新章节
                current_section = match.group(1).strip()
                current_content = []
            else:
                if current_section:
                    current_content.append(line)

        # 保存最后一个章节
        if current_section and current_content:
            sections[current_section] = '\n'.join(current_content).strip()

        return sections

    def _determine_fragment_kind(self, section_title: str) -> ContextKind:
        """
        根据章节标题确定片段类型

        Args:
            section_title: 章节标题

        Returns:
            ContextKind
        """
        section_lower = section_title.lower()

        if any(keyword in section_lower for keyword in ['专业领域', 'expertise', 'domain', '领域']):
            return ContextKind.SOUL
        elif any(keyword in section_lower for keyword in ['核心能力', 'capability', 'skill', '能力']):
            return ContextKind.SOUL
        elif any(keyword in section_lower for keyword in ['工作经历', 'experience', 'history', '经历']):
            return ContextKind.SOUL
        elif any(keyword in section_lower for keyword in ['职责', 'responsibility', 'duty', '职责']):
            return ContextKind.SOUL
        else:
            return ContextKind.SOUL  # 默认，soul_md 内容都属于 SOUL 类型

    def _determine_fragment_kind_from_name(self, name: str) -> ContextKind:
        """
        根据文件名确定 ContextKind（用于扩展内容）

        Args:
            name: 文件名，如 "docs/api.md", "custom.md"

        Returns:
            ContextKind
        """
        name_lower = name.lower()

        # 工具/接口相关
        if any(kw in name_lower for kw in ["tool", "api", "接口", "工具"]):
            return ContextKind.TOOLS

        # 工作流/流程相关
        if any(kw in name_lower for kw in ["workflow", "agent", "流程", "工作"]):
            return ContextKind.AGENT

        # 启动/配置相关
        if any(kw in name_lower for kw in ["boot", "config", "启动", "配置"]):
            return ContextKind.BOOT

        # 心跳/监控相关
        if any(kw in name_lower for kw in ["heartbeat", "monitor", "心跳", "监控"]):
            return ContextKind.HEARTBEAT

        # 技能相关
        if any(kw in name_lower for kw in ["skill", "能力", "技能"]):
            return ContextKind.SKILL

        # 默认 → other
        return ContextKind.OTHER

    def _extract_keywords(self, text: str) -> str:
        """
        从文本中提取关键词

        简单实现：提取所有中英文词汇

        Args:
            text: 输入文本

        Returns:
            关键词字符串
        """
        # 移除 Markdown 标记
        text = re.sub(r'[#*\-_`]', ' ', text)

        # 提取中英文词汇（简单实现）
        # 匹配中文词汇
        chinese_words = re.findall(r'[\u4e00-\u9fa5]+', text)

        # 匹配英文词汇
        english_words = re.findall(r'[a-zA-Z]+', text)

        # 合并
        keywords = chinese_words + english_words

        return ' '.join(keywords)

    def refresh(self) -> None:
        """
        刷新缓存，重新扫描

        Phase G6H Fix: This method is now a no-op since scan() no longer caches results.
        Kept for backward compatibility.

        Note: scan() now performs direct DB query every time, matching root_original behavior.
        """
        # Phase G6H Fix: No-op since scan() no longer caches
        # self._scan_result = None
        logger.info("[APIProfileSource] refresh() called (no-op, scan no longer caches)")


__all__ = ["APIProfileSource"]