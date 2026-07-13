"""
Worker Profile Content Service

Profile API MVP - Profile 内容管理服务

提供 Profile 的 CRUD 和活跃状态管理。
🔧 新方案：向量归属 Profile，Profile 内容变化时自动重建向量。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING, Dict, List

from src.domain.models.worker_profile_content import (
    ProfileContentType,
    SkillSet,
    WorkerProfileContent,
    WorkerProfileContentList,
)


class ProfilePatchResult:
    """
    Profile 增量更新结果

    包含更新后的 profile 以及是否发生了内容变化
    """
    def __init__(self, profile: WorkerProfileContent, content_changed: bool = False):
        self.profile = profile
        self.content_changed = content_changed

if TYPE_CHECKING:
    from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer

logger = logging.getLogger(__name__)


class WorkerProfileContentService:
    """
    Worker Profile Content 管理服务

    职责：
    - Profile CRUD
    - 活跃 Profile 管理
    - 🔧 新方案：Profile 内容变化时自动重建向量
    - 与 Worker Registry 协同
    """

    def __init__(
        self,
        store,
        vector_indexer: Optional["ProfileEmbeddingIndexer"] = None,
        registry_store = None,
        runtime_state_store = None,
        profile_store = None,
    ):
        """
        初始化服务

        Args:
            store: WorkerProfileContentStoreAdapter 实现
            vector_indexer: Profile Embedding Indexer（可选，用于内容变化时重建向量）
            registry_store: Worker Registry Store（可选，用于获取 worker 状态）
            runtime_state_store: Worker Runtime State Store（可选，用于获取 runtime_state）
            profile_store: Profile Embedding Store（可选，用于同步 worker 状态到向量）
        """
        self._store = store
        self._vector_indexer = vector_indexer
        self._registry_store = registry_store
        self._runtime_state_store = runtime_state_store
        self._profile_store = profile_store

    def register_or_update_profile(
        self,
        worker_id: str,
        profile_id: str,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        soul_md: Optional[str] = None,
        agents_md: Optional[str] = None,
        tools_md: Optional[str] = None,
        boot_md: Optional[str] = None,
        heartbeat_md: Optional[str] = None,
        contents: Optional[dict[str, Any]] = None,
        skill_sets: Optional[list[dict]] = None,
        metadata: Optional[dict] = None,
        activate: bool = False,
    ) -> WorkerProfileContent:
        """
        注册或更新 Profile

        🔧 新方案：自动检测内容变化，变化时重建向量索引。

        LLM 分析结果通过 contents 字段存储：
        - contents["profile"]: 语义能力画像
        - contents["capabilities"]: 能力标签列表

        Args:
            worker_id: Worker ID
            profile_id: Profile ID
            display_name: 显示名称
            description: 描述
            soul_md: SOUL.md 内容
            agents_md: AGENTS.md 内容
            tools_md: TOOLS.md 内容
            boot_md: BOOT.md 内容
            heartbeat_md: HEARTBEAT.md 内容
            contents: 扩展内容 JSON Map，支持任意 md 文件，也包含 LLM 分析结果
            skill_sets: 技能集列表
            metadata: 扩展元数据
            activate: 是否设为活跃

        Returns:
            保存后的 Profile 内容
        """
        profile_id = profile_id or "default"
        profile_key = f"{worker_id}:{profile_id}"

        # 🔧 Step 1: 获取旧 Profile（用于检测内容变化）
        old_profile = self._store.get(worker_id, profile_id)
        old_content_hash = self._compute_content_hash(old_profile) if old_profile else None

        # 构建 SkillSet 列表
        skills = []
        if skill_sets:
            for s in skill_sets:
                if isinstance(s, SkillSet):
                    skills.append(s)
                elif isinstance(s, dict):
                    skills.append(SkillSet(**s))

        # 构建 Profile 内容
        # LLM 分析结果已在 contents 中：contents["profile"], contents["capabilities"]
        content = WorkerProfileContent(
            worker_id=worker_id,
            profile_id=profile_id,
            display_name=display_name,
            description=description,
            soul_md=soul_md,
            agents_md=agents_md,
            tools_md=tools_md,
            boot_md=boot_md,
            heartbeat_md=heartbeat_md,
            contents=contents or {},
            skill_sets=skills,
            metadata=metadata or {},
            content_type=ProfileContentType.API,
        )

        # 计算新内容的 hash
        new_content_hash = self._compute_content_hash(content)
        content_changed = (old_content_hash != new_content_hash)

        # 🔧 Step 2: 保存 Profile
        saved = self._store.save(content)

        # 🔧 Step 3: 智能更新向量索引
        # 使用 update_index_smart：只重新计算真正变化的 fragments
        if self._vector_indexer:
            try:
                # 转换为 WorkerProfile 用于索引
                worker_profile = self._convert_to_worker_profile(saved)

                # 获取 worker 状态（用于 payload 中的 availability 和 runtime_state）
                worker_states = self._get_worker_states(worker_id)

                # 使用智能更新策略
                result = self._vector_indexer.update_index_smart([worker_profile], worker_states=worker_states)

                # 根据结果记录日志
                if content_changed:
                    logger.info(
                        f"[ProfileService] Smart update completed for {profile_key}: "
                        f"indexed={result.indexed_count}, failed={result.failed_count}, "
                        f"worker_states={'included' if worker_states else 'none'}"
                    )
                else:
                    logger.debug(
                        f"[ProfileService] Profile saved (no content change): {profile_key}"
                    )
            except Exception as e:
                logger.warning(f"[ProfileService] Smart update failed for {profile_key}: {e}")
                # 降级到简单更新策略
                try:
                    worker_profile = self._convert_to_worker_profile(saved)
                    worker_states = self._get_worker_states(worker_id)
                    result = self._vector_indexer.update_index([worker_profile], worker_states=worker_states)
                    logger.info(
                        f"[ProfileService] Fallback to simple update for {profile_key}: "
                        f"indexed={result.indexed_count}"
                    )
                except Exception as fallback_e:
                    logger.error(f"[ProfileService] Fallback update also failed: {fallback_e}")
                # 向量更新失败不应影响主流程
        else:
            logger.debug(f"[ProfileService] No vector indexer available for {profile_key}")

        # 如果需要激活
        if activate:
            self._store.activate(worker_id, profile_id)
            saved.is_active = True

        logger.info(
            f"[ProfileService] Profile registered/updated: worker={worker_id}, profile={profile_id}, "
            f"active={activate}, content_changed={content_changed}, contents_count={len(contents or {})}"
        )

        return saved

    def _compute_content_hash(self, profile: WorkerProfileContent) -> str:
        """
        计算 Profile 内容哈希（用于检测变化）

        Args:
            profile: Profile 内容

        Returns:
            SHA256 哈希值
        """
        # 提取影响向量的关键字段
        content_data = {
            "description": profile.description or "",
            "soul_md": profile.soul_md or "",
            "agents_md": profile.agents_md or "",
            "tools_md": profile.tools_md or "",
            "boot_md": profile.boot_md or "",
            "heartbeat_md": profile.heartbeat_md or "",
            "contents": profile.contents or {},
            "skill_sets": [
                {"name": s.name, "content": s.content}
                for s in (profile.skill_sets or [])
            ],
        }
        content_str = json.dumps(content_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content_str.encode()).hexdigest()[:16]

    def _convert_to_worker_profile(self, content: WorkerProfileContent) -> "WorkerProfile":
        """
        将 ProfileContent 转换为 WorkerProfile（用于索引）

        Args:
            content: Profile 内容

        Returns:
            WorkerProfile 对象
        """
        from src.domain.models.worker_profile import WorkerProfile, ProfileType, SourceType
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile
        from src.infra.config.content_embedding_config import ContentEmbeddingConfig

        # 构建 context_fragments
        context_fragments = []
        api_source = f"api://{content.worker_id}/{content.profile_id}"

        # 获取启用的向量化字段
        enabled_fields = ContentEmbeddingConfig.get_embedding_fields()

        # 核心字段映射（字段名 -> 内容）
        core_fields = {
            'soul': content.soul_md,
            'agents': content.agents_md,
            'tools': content.tools_md,
            'boot': content.boot_md,
            'heartbeat': content.heartbeat_md,
        }

        # Step 1: 处理核心字段（只处理启用了向量化的）
        for field_name, field_content in core_fields.items():
            if field_content and field_content.strip() and field_name in enabled_fields:
                context_fragments.append(ContextFragment(
                    filename=f"{field_name}.md",
                    content=field_content,
                    source_path=api_source,
                    metadata={"embedding_field": field_name},
                ))

        # Step 2: 处理 contents 字段（只处理启用的字段）
        for filename, file_content in (content.contents or {}).items():
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

            context_fragments.append(ContextFragment(
                filename=filename,
                content=content_str,
                source_path=f"{api_source}/{filename}",
                metadata={
                    "original_name": filename,
                    "embedding_field": field_name,
                },
            ))

        # 构建 active_skills（SkillProfile 需要 skill_id 和 skill_set_name）
        active_skills = []
        for s in (content.skill_sets or []):
            # 使用 skill name 作为 skill_id 和 skill_set_name 的备选
            skill_id = s.name.lower().replace(" ", "_")
            active_skills.append(SkillProfile(
                name=s.name,
                description=s.description,
                skill_id=skill_id,
                skill_set_name="default",  # 从 API 注册的 skill 使用 default 技能组
                is_active=True,
            ))

        # 将 skill_sets 作为单独的 context_fragment（如果启用了向量化）
        if "skill_sets" in enabled_fields and content.skill_sets:
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
                    filename="skill_sets.md",
                    content=skill_content,
                    source_path=api_source,
                    metadata={
                        "embedding_field": "skill_sets",
                        "original_name": "skill_sets",
                    },
                ))

        # 提取 short_profile（从 contents 中）
        short_profile = content.contents.get("short_profile", "") if content.contents else ""

        return WorkerProfile(
            staff_id=content.worker_id,
            profile_id=content.profile_id,
            profile_type=ProfileType.BOT,  # API 注册的 profile 默认为 BOT 类型
            source_type=SourceType.API,     # 来源为 API
            source_root=f"api://{content.worker_id}/{content.profile_id}",
            context_fragments=context_fragments,
            active_skills=active_skills,
            short_profile=short_profile,  # 传递精简画像
        )

    def _get_worker_states(self, worker_id: str) -> dict[str, dict] | None:
        """
        获取指定 worker 的状态信息

        Args:
            worker_id: Worker ID

        Returns:
            Worker 状态字典，格式: {staff_id: {"availability": ..., "runtime_state": ...}}
            如果无法获取，返回 None
        """
        logger.info(f"[_get_worker_states] START: worker_id={worker_id}, registry_store={self._registry_store is not None}, runtime_store={self._runtime_state_store is not None}")

        if not self._registry_store or not self._runtime_state_store:
            logger.warning(f"[_get_worker_states] FAILED: registry_store={self._registry_store is not None}, runtime_store={self._runtime_state_store is not None}")
            return None

        try:
            # 查询 worker
            logger.info(f"[_get_worker_states] Looking up worker: {worker_id}")
            worker = self._registry_store.get_by_id(worker_id)
            if not worker:
                logger.warning(f"[_get_worker_states] Worker not found: {worker_id}")
                return None

            logger.info(f"[_get_worker_states] Worker found: id={worker.id}, handle={worker.identity.handle}, availability={worker.state.availability.value}")

            # 获取 runtime state
            from src.domain.models.worker_runtime_state import WorkerRuntimeState
            runtime_state = self._runtime_state_store.get_runtime_state(worker_id)
            logger.info(f"[_get_worker_states] Runtime state: {runtime_state.value if runtime_state else 'OFFLINE'}")

            state_info = {
                "availability": worker.state.availability.value,
                "runtime_state": runtime_state.value if runtime_state else WorkerRuntimeState.OFFLINE.value,
            }

            # 使用多种 key 映射，确保 indexer 能找到
            worker_states = {worker_id: state_info}

            # 也使用 handle（去掉 @）作为 key
            handle = worker.identity.handle
            if handle and handle.startswith("@"):
                staff_id = handle[1:]  # 去掉 @ 前缀
                worker_states[staff_id] = state_info
                logger.info(f"[_get_worker_states] Added handle key: {staff_id}")

            # 也使用 external_id（如果存在）
            if hasattr(worker, 'external_id') and worker.external_id:
                worker_states[worker.external_id] = state_info
                logger.info(f"[_get_worker_states] Added external_id key: {worker.external_id}")

            logger.info(f"[_get_worker_states] SUCCESS: worker_id={worker_id}, state={state_info}, keys={list(worker_states.keys())}")
            return worker_states

        except Exception as e:
            logger.error(f"[_get_worker_states] EXCEPTION: worker_id={worker_id}, error={e}", exc_info=True)
            return None

    def get_profile(
        self, worker_id: str, profile_id: str = "default"
    ) -> Optional[WorkerProfileContent]:
        """
        获取 Profile

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            Profile 内容或 None
        """
        return self._store.get(worker_id, profile_id)

    def list_profiles(self, worker_id: str) -> WorkerProfileContentList:
        """
        列出 Worker 的所有 Profiles

        Args:
            worker_id: Worker ID

        Returns:
            Profile 列表
        """
        return self._store.list_by_worker(worker_id)

    def delete_profile(self, worker_id: str, profile_id: str = "default") -> bool:
        """
        删除 Profile

        🔧 新方案：先删除向量，再删除 Profile 记录。

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            是否删除成功
        """
        profile_key = f"{worker_id}:{profile_id}"
        logger.info("[ProfileService] Starting delete_profile for %s", profile_key)

        # 🔧 Step 1: 删除向量（如果 indexer 可用）
        if self._vector_indexer:
            logger.info("[ProfileService] Vector indexer available, deleting vectors for %s", profile_key)
            try:
                deleted_count = self._vector_indexer.delete_by_profile(profile_key)
                logger.info("[ProfileService] Vectors deleted for %s: count=%d", profile_key, deleted_count)
            except Exception as e:
                logger.error("[ProfileService] Failed to delete vectors for %s: %s", profile_key, e, exc_info=True)
                # 向量删除失败不应影响主流程
        else:
            logger.warning("[ProfileService] Vector indexer NOT available, skipping vector deletion for %s", profile_key)

        # Step 2: 删除 Profile 记录
        logger.info(f"[ProfileService] Deleting profile record for {profile_key}")
        result = self._store.delete(worker_id, profile_id)
        logger.info(f"[ProfileService] Profile record deleted: {profile_key}, result={result}")

        return result

    def activate_profile(
        self, worker_id: str, profile_id: str = "default"
    ) -> Optional[WorkerProfileContent]:
        """
        激活 Profile

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            激活后的 Profile 或 None
        """
        # 检查 profile 是否存在
        if not self._store.exists(worker_id, profile_id):
            logger.warning(
                f"Cannot activate: profile not found: worker={worker_id}, profile={profile_id}"
            )
            return None

        result = self._store.activate(worker_id, profile_id)

        if result:
            logger.info(f"[ProfileService] Profile activated: worker={worker_id}, profile={profile_id}")

        return result

    def get_active_profile(self, worker_id: str) -> Optional[WorkerProfileContent]:
        """
        获取活跃 Profile

        Args:
            worker_id: Worker ID

        Returns:
            活跃 Profile 或 None
        """
        return self._store.get_active(worker_id)

    def get_all_active_profiles(self) -> list[WorkerProfileContent]:
        """
        获取所有活跃 Profile

        用于检索/推荐系统批量加载。

        Returns:
            所有活跃 Profile 列表
        """
        return self._store.get_all_active()

    def profile_exists(self, worker_id: str, profile_id: str = "default") -> bool:
        """
        检查 Profile 是否存在

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            是否存在
        """
        return self._store.exists(worker_id, profile_id)

    def patch_profile(
        self,
        worker_id: str,
        profile_id: str,
        display_name: Optional[str] = None,
        soul_md: Optional[str] = None,
        agents_md: Optional[str] = None,
        tools_md: Optional[str] = None,
        boot_md: Optional[str] = None,
        heartbeat_md: Optional[str] = None,
        contents: Optional[dict[str, Any]] = None,
        contents_delete: Optional[list[str]] = None,
        skill_sets: Optional[list[dict]] = None,
        metadata: Optional[dict] = None,
        metadata_delete: Optional[list[str]] = None,
        activate: bool = False,
    ) -> ProfilePatchResult:
        """
        增量更新 Profile

        只更新传入的字段，未传入的字段保持不变。
        对于 contents 和 metadata JSON 字段：
        - 传入的键值对会更新或新增
        - 如果指定了 contents_delete 或 metadata_delete，对应的键会被删除

        🔧 逻辑：
        1. 获取现有 profile
        2. 合并字段：基本字段优先使用传入值，contents/metadata 增量合并
        3. 保存 profile
        4. 检测内容变化，重建向量

        Args:
            worker_id: Worker ID
            profile_id: Profile ID
            display_name: 显示名称（可选）
            soul_md: SOUL.md 内容（可选）
            agents_md: AGENTS.md 内容（可选）
            tools_md: TOOLS.md 内容（可选）
            boot_md: BOOT.md 内容（可选）
            heartbeat_md: HEARTBEAT.md 内容（可选）
            contents: 扩展内容增量更新（可选）
            contents_delete: 要删除的 contents 键列表（可选）
            skill_sets: 技能集列表（可选，传空列表会清空）
            metadata: 元数据增量更新（可选）
            metadata_delete: 要删除的 metadata 键列表（可选）
            activate: 是否设为活跃

        Returns:
            ProfilePatchResult 包含更新后的 profile 和内容变化标志

        Raises:
            ValueError: 如果 profile 不存在
        """
        profile_id = profile_id or "default"
        profile_key = f"{worker_id}:{profile_id}"

        # Step 1: 获取现有 profile
        existing = self._store.get(worker_id, profile_id)
        if existing is None:
            raise ValueError(f"Profile not found: {profile_key}")

        # Step 2: 计算旧内容 hash（用于检测变化）
        old_content_hash = self._compute_content_hash(existing)

        # Step 3: 合并基本字段（None 表示不更新）
        new_display_name = display_name if display_name is not None else existing.display_name
        new_soul_md = soul_md if soul_md is not None else existing.soul_md
        new_agents_md = agents_md if agents_md is not None else existing.agents_md
        new_tools_md = tools_md if tools_md is not None else existing.tools_md
        new_boot_md = boot_md if boot_md is not None else existing.boot_md
        new_heartbeat_md = heartbeat_md if heartbeat_md is not None else existing.heartbeat_md

        # Step 4: 合并 contents（增量更新）
        new_contents = dict(existing.contents) if existing.contents else {}
        if contents:
            for key, value in contents.items():
                if value is not None:
                    new_contents[key] = value
        if contents_delete:
            for key in contents_delete:
                new_contents.pop(key, None)

        # Step 5: 合并 skill_sets（如果有传入则替换，否则保留原值）
        new_skill_sets = existing.skill_sets
        if skill_sets is not None:
            new_skill_sets = []
            for s in skill_sets:
                if isinstance(s, SkillSet):
                    new_skill_sets.append(s)
                elif isinstance(s, dict):
                    new_skill_sets.append(SkillSet(**s))

        # Step 6: 合并 metadata（增量更新）
        new_metadata = dict(existing.metadata) if existing.metadata else {}
        if metadata:
            for key, value in metadata.items():
                if value is not None:
                    new_metadata[key] = value
        if metadata_delete:
            for key in metadata_delete:
                new_metadata.pop(key, None)

        # Step 7: 构建新的 profile
        merged_content = WorkerProfileContent(
            worker_id=worker_id,
            profile_id=profile_id,
            display_name=new_display_name,
            soul_md=new_soul_md,
            agents_md=new_agents_md,
            tools_md=new_tools_md,
            boot_md=new_boot_md,
            heartbeat_md=new_heartbeat_md,
            contents=new_contents,
            skill_sets=new_skill_sets,
            metadata=new_metadata,
            content_type=existing.content_type,
            is_active=existing.is_active,
            version=existing.version,
            created_at=existing.created_at,
        )

        # Step 8: 保存 profile
        saved = self._store.save(merged_content)

        # Step 9: 检测内容变化
        new_content_hash = self._compute_content_hash(saved)
        content_changed = (old_content_hash != new_content_hash)

        # Step 10: 如果内容变化，重建向量
        if content_changed and self._vector_indexer:
            logger.info(
                f"[ProfileService] Content changed after patch for {profile_key}, "
                f"rebuilding vectors..."
            )
            try:
                worker_profile = self._convert_to_worker_profile(saved)
                result = self._vector_indexer.update_index([worker_profile])
                logger.info(
                    f"[ProfileService] Vectors rebuilt for {profile_key}: "
                    f"indexed={result.indexed_count}"
                )
            except Exception as e:
                logger.warning(f"[ProfileService] Failed to rebuild vectors for {profile_key}: {e}")

        # Step 11: 如果需要激活
        if activate and not saved.is_active:
            self._store.activate(worker_id, profile_id)
            saved.is_active = True

        logger.info(
            f"[ProfileService] Profile patched: worker={worker_id}, profile={profile_id}, "
            f"activate={activate}, content_changed={content_changed}, "
            f"contents_updated={contents is not None}, contents_deleted={contents_delete or []}"
        )

        return ProfilePatchResult(profile=saved, content_changed=content_changed)

    def sync_worker_state_to_vectors(
        self,
        worker_id: str,
    ) -> bool:
        """
        同步 Worker 状态到向量 Payload

        当 Worker 的 availability 或 runtime_state 变化时，
        调用此方法更新向量索引中的 payload，而不重新计算 embedding。

        使用场景：
        - Worker 设置为 online/offline
        - Worker availability 从 public 变为 private

        Args:
            worker_id: Worker ID

        Returns:
            是否成功更新
        """
        if not self._vector_indexer:
            logger.debug(f"[ProfileService] No vector indexer available, skip sync")
            return False

        try:
            # 获取 Worker 的所有 profiles
            profiles_result = self._store.list_by_worker(worker_id)
            if not profiles_result.items:
                logger.debug(f"[ProfileService] No profiles for {worker_id}, skip sync")
                return False

            # 获取 worker 状态
            worker_states = self._get_worker_states(worker_id)

            updated_count = 0
            for profile_content in profiles_result.items:
                profile_key = f"{worker_id}:{profile_content.profile_id}"

                try:
                    # 获取已有的 fragments
                    existing_fragments = self._profile_store.get_fragments_by_profile(profile_key)

                    if not existing_fragments:
                        logger.debug(f"[ProfileService] No existing vectors for {profile_key}")
                        continue

                    # 准备 payload 更新
                    payload_updates = []
                    for fragment_id, vector, old_payload in existing_fragments:
                        if not old_payload:
                            continue

                        # 检查状态是否需要更新
                        old_availability = old_payload.get("availability")
                        old_runtime_state = old_payload.get("runtime_state")

                        new_availability = worker_states.get(worker_id, {}).get("availability")
                        new_runtime_state = worker_states.get(worker_id, {}).get("runtime_state")

                        if old_availability != new_availability or old_runtime_state != new_runtime_state:
                            new_payload = dict(old_payload)
                            if new_availability is not None:
                                new_payload["availability"] = new_availability
                            if new_runtime_state is not None:
                                new_payload["runtime_state"] = new_runtime_state
                            new_payload["indexed_at"] = datetime.now().isoformat()
                            payload_updates.append((fragment_id, new_payload))

                    # 执行更新
                    if payload_updates:
                        updated = self._profile_store.update_payloads(payload_updates)
                        updated_count += updated
                        logger.info(
                            f"[ProfileService] Synced worker state for {profile_key}: "
                            f"updated={updated}/{len(payload_updates)} fragments, "
                            f"availability={worker_states.get(worker_id, {}).get('availability')}, "
                            f"runtime_state={worker_states.get(worker_id, {}).get('runtime_state')}"
                        )
                    else:
                        logger.debug(f"[ProfileService] Worker state unchanged for {profile_key}")

                except Exception as e:
                    logger.warning(f"[ProfileService] Failed to sync state for {profile_key}: {e}")
                    continue

            return updated_count > 0

        except Exception as e:
            logger.error(f"[ProfileService] Failed to sync worker state to vectors: {e}")
            return False


__all__ = ["WorkerProfileContentService", "ProfilePatchResult"]