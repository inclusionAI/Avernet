"""
Worker Profile API Routes

Profile API MVP Phase 2 - Profile 内容管理 API 端点

端点：
- PUT /v1/workers/{worker_id}/profiles/{profile_id} - 创建/更新 Profile
- GET /v1/workers/{worker_id}/profiles/{profile_id} - 获取 Profile
- DELETE /v1/workers/{worker_id}/profiles/{profile_id} - 删除 Profile
- GET /v1/workers/{worker_id}/profiles - 列出 Profiles
- PUT /v1/workers/{worker_id}/profiles/{profile_id}/activate - 激活 Profile

Phase 2 联动：
- activate 时同步更新 worker_profile_bindings
- activate 时同步更新 workers.active_profile_key
- profile 变更后触发索引同步
"""

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.application.services.worker_profile_content_service import WorkerProfileContentService
from src.domain.models.worker_profile_content import (
    WorkerProfileContent,
    WorkerProfileContentList,
)
from src.domain.models.worker_source_info import WorkerSourceType


logger = logging.getLogger(__name__)


router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class SkillSetRequest(BaseModel):
    """技能集请求"""
    name: str = Field(..., min_length=1, description="技能名称")
    description: Optional[str] = Field(None, description="技能描述")
    content: Optional[str] = Field(None, description="技能详细内容")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


class ProfileRequest(BaseModel):
    """Profile 创建/更新请求"""
    display_name: Optional[str] = Field(None, description="显示名称")
    soul_md: Optional[str] = Field(None, description="SOUL.md 内容 - 核心身份")
    agents_md: Optional[str] = Field(None, description="AGENTS.md 内容 - 工作配置")
    tools_md: Optional[str] = Field(None, description="TOOLS.md 内容 - 工具配置")
    boot_md: Optional[str] = Field(None, description="BOOT.md 内容 - 启动配置")
    heartbeat_md: Optional[str] = Field(None, description="HEARTBEAT.md 内容")
    contents: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展内容 JSON Map，支持任意类型。例如: {\"profile\": \"内容\", \"capabilities\": [\"技能1\", \"技能2\"]}"
    )
    skill_sets: list[SkillSetRequest] = Field(default_factory=list, description="技能集")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")
    activate: bool = Field(default=False, description="是否设为活跃")


class ProfilePatchRequest(BaseModel):
    """Profile 增量更新请求

    只更新传入的字段，未传入的字段保持不变。
    - contents: 增量更新，传入的键值对会新增或替换，不会删除未传入的键
    - contents_delete: 指定要删除的 contents 键
    - metadata: 增量更新，类似 contents
    - metadata_delete: 指定要删除的 metadata 键
    - skill_sets: 如果传入则替换全部（无法部分更新技能）
    """
    display_name: Optional[str] = Field(None, description="显示名称（不传则保持不变）")
    soul_md: Optional[str] = Field(None, description="SOUL.md 内容（不传则保持不变）")
    agents_md: Optional[str] = Field(None, description="AGENTS.md 内容（不传则保持不变）")
    tools_md: Optional[str] = Field(None, description="TOOLS.md 内容（不传则保持不变）")
    boot_md: Optional[str] = Field(None, description="BOOT.md 内容（不传则保持不变）")
    heartbeat_md: Optional[str] = Field(None, description="HEARTBEAT.md 内容（不传则保持不变）")
    contents: Optional[dict[str, Any]] = Field(
        None,
        description="扩展内容增量更新，传入的键值对会新增或替换，支持任意类型。例如: {\"capabilities\": [\"技能1\", \"技能2\"]}"
    )
    contents_delete: Optional[list[str]] = Field(
        None,
        description="要删除的 contents 键列表。例如: [\"old.md\", \"temp.md\"]"
    )
    skill_sets: Optional[list[SkillSetRequest]] = Field(
        None,
        description="技能集列表（传入则替换全部，不传则保持不变）"
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="元数据增量更新，传入的键值对会新增或替换"
    )
    metadata_delete: Optional[list[str]] = Field(
        None,
        description="要删除的 metadata 键列表"
    )
    activate: bool = Field(default=False, description="是否设为活跃")


class ProfileResponse(BaseModel):
    """Profile 响应"""
    worker_id: str
    profile_id: str
    display_name: Optional[str]
    soul_md: Optional[str]
    agents_md: Optional[str]
    tools_md: Optional[str]
    boot_md: Optional[str]
    heartbeat_md: Optional[str]
    contents: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展内容 JSON Map，包含 profile(语义画像) 和 capabilities(能力标签)"
    )
    skill_sets: list[dict]
    metadata: dict
    content_type: str
    is_active: bool
    version: int
    quality_score: Optional[float] = None
    quality_issues: list[str] = Field(default_factory=list, description="质量问题列表")
    created_at: Optional[str]
    updated_at: Optional[str]

    # 兼容旧 API 的属性（从 contents 中提取）
    @property
    def semantic_profile(self) -> Optional[str]:
        """获取 LLM 生成的语义能力画像（兼容旧 API）"""
        return self.contents.get("profile")

    @property
    def capability_tags(self) -> list[str]:
        """获取 LLM 生成的能力标签列表（兼容旧 API）"""
        capabilities = self.contents.get("capabilities")
        if isinstance(capabilities, list):
            return [str(c) for c in capabilities]
        return []

    @classmethod
    def from_content(cls, content: WorkerProfileContent, include_quality: bool = True) -> "ProfileResponse":
        """从 WorkerProfileContent 创建响应，可选计算质量分数"""
        quality_score = None
        quality_issues = []

        if include_quality:
            try:
                from src.domain.services.profile_quality_scorer import ProfileQualityScorer
                profile_quality_scorer = ProfileQualityScorer()
                quality = profile_quality_scorer.score_profile_content(content)
                quality_score = round(quality.total_score, 2)  # 保留2位小数避免浮点精度问题
                quality_issues = quality.issues
            except Exception as e:
                logger.warning(f"Failed to calculate quality score: {e}")

        return cls(
            worker_id=content.worker_id,
            profile_id=content.profile_id,
            display_name=content.display_name,
            soul_md=content.soul_md,
            agents_md=content.agents_md,
            tools_md=content.tools_md,
            boot_md=content.boot_md,
            heartbeat_md=content.heartbeat_md,
            contents=content.contents or {},
            skill_sets=[s.model_dump() for s in content.skill_sets],
            metadata=content.metadata,
            content_type=content.content_type.value,
            is_active=content.is_active,
            version=content.version,
            quality_score=quality_score,
            quality_issues=quality_issues,
            created_at=content.created_at.isoformat() if content.created_at else None,
            updated_at=content.updated_at.isoformat() if content.updated_at else None,
        )


class ProfileListResponse(BaseModel):
    """Profile 列表响应"""
    items: list[ProfileResponse]
    total: int
    active_profile_id: Optional[str]


class ActivateResponse(BaseModel):
    """激活响应"""
    worker_id: str
    profile_id: str
    is_active: bool
    binding_updated: bool
    worker_updated: bool
    message: str


# ============================================================================
# Global Store Instance
# ============================================================================

_profile_content_store = None
_profile_service = None
_profile_analyzer = None


def _get_profile_content_store():
    """获取 Profile Content Store（单例）- SQLite only for open-core"""
    global _profile_content_store
    if _profile_content_store is None:
        # SQLite 模式：本地存储（open-core only）
        from src.infra.adapters.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
        from src.infra.config.worker_registry_settings import WorkerRegistrySettings

        settings = WorkerRegistrySettings()
        db_path = settings.get_effective_db_path()

        _profile_content_store = SQLiteWorkerProfileContentStore(db_path)
        logger.info(f"[Open-Core] ProfileContent store initialized (SQLite), path={db_path}")
    return _profile_content_store


def _get_profile_service():
    """获取 Profile Service（带向量索引器）- SQLite only for open-core"""
    global _profile_service
    if _profile_service is None:
        store = _get_profile_content_store()

        # 🔧 创建 vector indexer，使 ProfileService 能在内容变化时自动重建向量
        vector_indexer = None
        try:
            from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
            from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore
            from src.infra.embedding.config.embedding_settings import EmbeddingSettings
            from src.infra.embedding.providers.real_provider import RealEmbeddingProvider
            from src.infra.config.data_paths import resolve_data_path

            settings = EmbeddingSettings()

            # 检查 embedding 配置是否完整
            if settings.is_configured():
                # 创建 embedding provider
                embedding_provider = RealEmbeddingProvider(settings=settings)

                # 创建 profile embedding store - Open-core always uses local mode
                profile_store = ProfileEmbeddingStore(
                    dimension=settings.dimension,
                    index_type="local",  # Always local for open-core
                    db_path=resolve_data_path("data/vector_store.db"),
                    database=None,  # No database for open-core
                    datasource_name="agentclaw_ds",
                )

                # 创建 indexer
                vector_indexer = ProfileEmbeddingIndexer(
                    embedding_provider=embedding_provider,
                    profile_store=profile_store,
                )
                logger.info("[Open-Core] ProfileEmbeddingIndexer created successfully (local mode)")
            else:
                missing = settings.missing_config()
                logger.warning(f"[ProfileService] Embedding not configured, missing: {missing}. Vector auto-build disabled.")

        except Exception as e:
            logger.warning(f"[ProfileService] Failed to create ProfileEmbeddingIndexer: {e}. Vector auto-build disabled.")
            vector_indexer = None

        # 获取 registry 和 runtime state store（用于向量索引时获取 worker 状态）
        registry_store = None
        runtime_state_store = None
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import _get_registry_store, _get_runtime_state_store
            registry_store = _get_registry_store()
            runtime_state_store = _get_runtime_state_store()
            logger.info("[ProfileService] Registry and runtime state stores injected for vector indexing")
        except Exception as e:
            logger.warning(f"[ProfileService] Failed to get registry/runtime stores: {e}. Worker state will not be indexed.")

        _profile_service = WorkerProfileContentService(
            store,
            vector_indexer=vector_indexer,
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
        )
    return _profile_service


def _get_profile_analyzer():
    """获取 Profile Analyzer Service（单例）"""
    global _profile_analyzer
    if _profile_analyzer is None:
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import _get_llm_gateway_service
            from src.interfaces.api.dependencies.worker_dependencies import _get_bot_cognition_provider
            from src.application.services.profile_analyzer_service import ProfileAnalyzerService

            gateway = _get_llm_gateway_service()
            if gateway:
                cognition_provider = _get_bot_cognition_provider()
                _profile_analyzer = ProfileAnalyzerService(
                    llm_gateway=gateway,
                    cognition_provider=cognition_provider,
                )
                logger.info("[ProfileAnalyzer] Initialized with LLM Gateway and BotCognition Provider")
            else:
                logger.warning("[ProfileAnalyzer] LLM Gateway 不可用, 分析功能禁用")
        except Exception as e:
            logger.warning(f"[ProfileAnalyzer] 初始化失败: {e}")
    return _profile_analyzer


def _sync_profile_binding(worker_id: str, profile_id: str) -> bool:
    """
    同步 Profile Binding

    更新 worker_profile_bindings 表，将 profile_key 绑定到 worker_id。

    Args:
        worker_id: Worker ID
        profile_id: Profile ID

    Returns:
        是否同步成功
    """
    try:
        from src.interfaces.api.dependencies.worker_dependencies import _get_profile_binding_store

        profile_key = f"{worker_id}:{profile_id}"
        binding_store = _get_profile_binding_store()

        # 绑定 profile
        binding_store.bind_profile(
            worker_id=worker_id,
            profile_key=profile_key,
            source_type=WorkerSourceType.API,
        )

        logger.info(f"Profile binding updated: {profile_key}")
        return True

    except Exception as e:
        logger.warning(f"Failed to sync profile binding: {e}")
        return False


def _sync_worker_active_profile(worker_id: str, profile_id: str) -> bool:
    """
    同步 Worker 的 active_profile_key

    更新 workers 表的 active_profile_key 字段。

    Args:
        worker_id: Worker ID
        profile_id: Profile ID

    Returns:
        是否同步成功
    """
    try:
        from src.interfaces.api.dependencies.worker_dependencies import _get_registry_store

        registry_store = _get_registry_store()

        # 获取 worker
        worker = registry_store.get_by_id(worker_id)
        if worker is None:
            logger.warning(f"Worker not found: {worker_id}")
            return False

        # 更新 active_profile_key
        profile_key = f"{worker_id}:{profile_id}"

        # 通过 patch 更新
        worker_dict = worker.model_dump()
        worker_dict['active_profile_key'] = profile_key

        from src.domain.models.worker import Worker
        updated_worker = Worker.model_validate(worker_dict)

        registry_store.update(updated_worker)

        logger.info(f"Worker active_profile_key updated: {profile_key}")
        return True

    except Exception as e:
        logger.warning(f"Failed to sync worker active_profile_key: {e}")
        return False


def _trigger_index_sync(worker_id: str) -> None:
    """
    触发索引同步

    通知 Retrieval/Recommendation 系统更新索引。
    🔧 新方案：只重置服务缓存，向量重建由 Profile 层处理

    Args:
        worker_id: Worker ID
    """
    try:
        # 重置服务缓存（让下次请求时重新创建服务）
        from src.interfaces.api.dependencies.fusion_dependencies import reset_fusion_services
        reset_fusion_services()

        logger.info(f"[IndexSync] Service cache reset for worker: {worker_id}")

        # 🔧 新方案：向量归属 Profile，不再在这里重建
        # ProfileService.register_or_update_profile() 会在内容变化时自动重建向量

    except Exception as e:
        logger.warning(f"[IndexSync] Failed to reset service cache: {e}")


def _build_vector_index_incremental(worker_id: str) -> None:
    """
    增量构建向量索引 - SQLite only for open-core

    为指定worker的profile生成embedding并写入向量索引。

    Args:
        worker_id: Worker ID
    """
    try:
        logger.info(f"[INDEX-BUILD] 开始为worker构建向量索引: {worker_id}")

        from src.interfaces.api.dependencies.fusion_dependencies import (
            _get_embedding_generator,
            _get_profile_source,
        )
        from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
        from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore
        from src.infra.embedding.config.embedding_settings import EmbeddingSettings

        # 获取依赖
        embedding_gen = _get_embedding_generator()
        profile_src = _get_profile_source()

        if not embedding_gen:
            logger.warning("[INDEX-BUILD] Embedding generator不可用，跳过索引构建")
            logger.debug("[INDEX-BUILD] 可能原因：ENABLE_REAL_EMBEDDING=false 或配置缺失")
            return

        if not profile_src:
            logger.warning("[INDEX-BUILD] Profile source不可用，跳过索引构建")
            return

        # 扫描指定worker的profile
        scan_result = profile_src.scan()
        target_profile = None
        for profile in scan_result.profiles:
            if profile.profile_key.startswith(worker_id):
                target_profile = profile
                break

        if not target_profile:
            logger.debug(f"[INDEX-BUILD] 未找到worker的profile: {worker_id}")
            logger.debug("[INDEX-BUILD] 可能原因：profile尚未注册或profile_key格式不匹配")
            return

        logger.info(f"[INDEX-BUILD] 找到profile: {target_profile.profile_key}")
        logger.info(f"[INDEX-BUILD]   - 技能数: {len(target_profile.active_skills)}")
        logger.info(f"[INDEX-BUILD]   - 上下文片段数: {len(target_profile.context_fragments)}")

        # 创建索引存储 - Open-core always uses local mode
        settings = EmbeddingSettings()

        profile_store = ProfileEmbeddingStore(
            dimension=settings.dimension,
            index_type="local",  # Always local for open-core
            db_path="data/vector_store.db",  # 使用统一的数据库路径
            database=None,  # No database for open-core
            datasource_name="agentclaw_ds",
        )

        # 创建索引器
        indexer = ProfileEmbeddingIndexer(
            embedding_provider=embedding_gen,
            profile_store=profile_store,
        )

        # 智能增量索引：复用已有向量，只重新计算变更的 fragments
        logger.info(f"[INDEX-BUILD] 开始智能更新索引...")
        result = indexer.update_index_smart([target_profile])

        if result.indexed_count > 0:
            logger.info(
                f"✅ [INDEX-BUILD] 向量索引构建成功: worker={worker_id}, "
                f"profile_key={target_profile.profile_key}, "
                f"indexed={result.indexed_count}, "
                f"duration={result.duration_seconds:.2f}s"
            )

            # 检查索引状态（size 是 property，不是方法）
            index_size = profile_store.size
            logger.info(f"✅ [INDEX-BUILD] 当前向量索引总大小: {index_size}")
        else:
            logger.warning(
                f"⚠️ [INDEX-BUILD] 向量索引构建失败: worker={worker_id}"
            )
            if result.errors:
                for error in result.errors[:3]:
                    logger.warning(f"  - {error}")

    except Exception as e:
        logger.error(f"❌ [INDEX-BUILD] 向量索引构建异常: {e}")
        import traceback
        logger.error(traceback.format_exc())


# ============================================================================
# Dependencies
# ============================================================================

def get_profile_service():
    """获取 Profile Service"""
    return _get_profile_service()


# ============================================================================
# API Endpoints
# ============================================================================

@router.patch(
    "/workers/{worker_id}/profiles/{profile_id}",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="增量更新 Profile",
    description="只更新传入的字段，未传入的字段保持不变。对于 contents 和 metadata JSON 字段，只更新传入的键值对。",
)
async def patch_profile(
    worker_id: str,
    profile_id: str,
    request: ProfilePatchRequest,
    service: WorkerProfileContentService = Depends(get_profile_service),
):
    """
    增量更新 Profile 内容

    特点：
    - 只更新传入的字段，未传入的字段保持不变
    - contents: 增量更新，传入的键值对会新增或替换，不会删除未传入的键
    - metadata: 增量更新，类似 contents
    - contents_delete: 指定要删除的 contents 键
    - metadata_delete: 指定要删除的 metadata 键
    - skill_sets: 如果传入则替换全部（无法部分更新技能）

    示例请求：
    ```json
    {
        "display_name": "新名称",
        "contents": {"custom.md": "新内容"},
        "contents_delete": ["old.md"],
        "metadata": {"version": "2.0"},
        "metadata_delete": ["deprecated_key"]
    }
    ```
    """
    if not service.profile_exists(worker_id, profile_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PROFILE_NOT_FOUND",
                "message": f"Profile not found: worker={worker_id}, profile={profile_id}"
            }
        )

    try:
        result = service.patch_profile(
            worker_id=worker_id,
            profile_id=profile_id,
            display_name=request.display_name,
            soul_md=request.soul_md,
            agents_md=request.agents_md,
            tools_md=request.tools_md,
            boot_md=request.boot_md,
            heartbeat_md=request.heartbeat_md,
            contents=request.contents,
            contents_delete=request.contents_delete,
            skill_sets=[s.model_dump() for s in request.skill_sets] if request.skill_sets is not None else None,
            metadata=request.metadata,
            metadata_delete=request.metadata_delete,
            activate=request.activate,
        )

        # 如果需要激活，同步 binding 和 worker
        if request.activate:
            _sync_profile_binding(worker_id, profile_id)
            _sync_worker_active_profile(worker_id, profile_id)
            # 确保向量已构建
            _rebuild_profile_vectors(worker_id, profile_id)

        # 重置服务缓存
        if result.content_changed:
            _trigger_index_sync(worker_id)

        return ProfileResponse.from_content(result.profile)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROFILE_NOT_FOUND", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Profile patch error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PROFILE_PATCH_ERROR", "message": str(e)}
        )


@router.put(
    "/workers/{worker_id}/profiles/{profile_id}",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def upsert_profile(
    worker_id: str,
    profile_id: str,
    request: ProfileRequest,
    service: WorkerProfileContentService = Depends(get_profile_service),
):
    """
    创建或更新 Profile

    支持扩展内容：
    - 核心字段: soul_md, agents_md, tools_md, boot_md, heartbeat_md
    - 扩展内容: contents JSON Map，支持任意 md 文件
    """
    try:
        content = service.register_or_update_profile(
            worker_id=worker_id,
            profile_id=profile_id,
            display_name=request.display_name,
            soul_md=request.soul_md,
            agents_md=request.agents_md,
            tools_md=request.tools_md,
            boot_md=request.boot_md,
            heartbeat_md=request.heartbeat_md,
            contents=request.contents,
            skill_sets=[s.model_dump() for s in request.skill_sets],
            metadata=request.metadata,
            activate=request.activate,
        )

        # LLM 能力分析（非阻塞，失败不影响 profile 保存）
        analyzer = _get_profile_analyzer()
        if analyzer:
            try:
                analysis = analyzer.analyze(content)
                if analysis.llm_success:
                    # 将分析结果存入 contents 字段
                    content.contents["profile"] = analysis.semantic_profile
                    content.contents["capabilities"] = analysis.capability_tags
                    content.contents["short_profile"] = analysis.short_profile  # 新增：精简画像

                    # 重新保存以持久化分析结果
                    content = service.register_or_update_profile(
                        worker_id=worker_id,
                        profile_id=profile_id,
                        display_name=content.display_name,
                        soul_md=content.soul_md,
                        agents_md=content.agents_md,
                        tools_md=content.tools_md,
                        boot_md=content.boot_md,
                        heartbeat_md=content.heartbeat_md,
                        contents=content.contents,
                        skill_sets=[s.model_dump() for s in content.skill_sets],
                        metadata=content.metadata,
                        activate=request.activate,
                    )
                    logger.info(
                        f"[ProfileAnalysis] 分析完成: worker={worker_id}, "
                        f"tags={analysis.capability_tags}, "
                        f"short_profile={analysis.short_profile}"
                    )
                else:
                    logger.info(
                        f"[ProfileAnalysis] 分析未成功: {analysis.error_message}"
                    )
            except Exception as e:
                logger.warning(f"[ProfileAnalysis] 分析异常, 跳过: {e}")

        # 如果需要激活，同步 binding 和 worker
        if request.activate:
            _sync_profile_binding(worker_id, profile_id)
            _sync_worker_active_profile(worker_id, profile_id)
            # 🔧 新方案：如果 content 没变化不会重建向量，确保 activate 时向量存在
            _rebuild_profile_vectors(worker_id, profile_id)

        # 重置服务缓存
        _trigger_index_sync(worker_id)

        return ProfileResponse.from_content(content)

    except Exception as e:
        logger.error(f"Profile save error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PROFILE_SAVE_ERROR", "message": str(e)}
        )


@router.get(
    "/workers/{worker_id}/profiles/{profile_id}",
    response_model=ProfileResponse,
)
async def get_profile(
    worker_id: str,
    profile_id: str,
    service: WorkerProfileContentService = Depends(get_profile_service),
):
    """获取指定 Profile"""
    content = service.get_profile(worker_id, profile_id)

    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PROFILE_NOT_FOUND",
                "message": f"Profile not found: worker={worker_id}, profile={profile_id}"
            }
        )

    return ProfileResponse.from_content(content)


@router.get(
    "/workers/{worker_id}/profiles",
    response_model=ProfileListResponse,
)
async def list_profiles(
    worker_id: str,
    service: WorkerProfileContentService = Depends(get_profile_service),
):
    """列出 Worker 的所有 Profiles"""
    result = service.list_profiles(worker_id)

    return ProfileListResponse(
        items=[ProfileResponse.from_content(c) for c in result.items],
        total=result.total,
        active_profile_id=result.active_profile_id,
    )


@router.delete(
    "/workers/{worker_id}/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_profile(
    worker_id: str,
    profile_id: str,
    service: WorkerProfileContentService = Depends(get_profile_service),
):
    """
    删除 Profile

    删除操作会级联删除：
    - Profile 内容数据
    - 向量索引数据（防止脏数据）
    """
    if not service.profile_exists(worker_id, profile_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PROFILE_NOT_FOUND",
                "message": f"Profile not found: worker={worker_id}, profile={profile_id}"
            }
        )

    deleted = service.delete_profile(worker_id, profile_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PROFILE_DELETE_ERROR", "message": "Failed to delete profile"}
        )

    # 🔧 新方案：ProfileService.delete_profile() 内部已删除向量
    # 此处无需重复操作

    # 重置服务缓存
    _trigger_index_sync(worker_id)


@router.put(
    "/workers/{worker_id}/profiles/{profile_id}/activate",
    response_model=ActivateResponse,
)
async def activate_profile(
    worker_id: str,
    profile_id: str,
    service: WorkerProfileContentService = Depends(get_profile_service),
):
    """
    激活 Profile

    将指定 Profile 设为活跃，并同步：
    - worker_profile_bindings
    - workers.active_profile_key
    """
    if not service.profile_exists(worker_id, profile_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PROFILE_NOT_FOUND",
                "message": f"Profile not found: worker={worker_id}, profile={profile_id}"
            }
        )

    # 🆕 获取之前的活跃 profile（用于删除旧向量）
    old_active_profile = service.get_active_profile(worker_id)
    old_profile_id = old_active_profile.profile_id if old_active_profile else None

    # 激活 Profile
    content = service.activate_profile(worker_id, profile_id)

    # 同步 binding 和 worker
    binding_updated = _sync_profile_binding(worker_id, profile_id)
    worker_updated = _sync_worker_active_profile(worker_id, profile_id)

    # 🔧 新方案：activate 不涉及内容变化，不删旧 profile 向量
    # 但需确保新 active profile 有向量（如果是首次激活）
    if not old_profile_id:
        # 首次激活，确保向量已构建
        logger.info(f"[ProfileRoutes] First-time profile activation for worker {worker_id}, ensure vectors exist...")
        _rebuild_profile_vectors(worker_id, profile_id)
    elif old_profile_id != profile_id:
        logger.info(f"[ProfileRoutes] Profile switched from {old_profile_id} to {profile_id} for worker {worker_id}")
        # Note: 不删除旧 profile 向量，以便切回时复用

    # 重置服务缓存
    _trigger_index_sync(worker_id)

    return ActivateResponse(
        worker_id=worker_id,
        profile_id=profile_id,
        is_active=True,
        binding_updated=binding_updated,
        worker_updated=worker_updated,
        message=f"Profile {profile_id} activated for worker {worker_id}",
    )


@router.get(
    "/workers/{worker_id}/profiles/active",
    response_model=ProfileResponse,
)
async def get_active_profile(
    worker_id: str,
    service: WorkerProfileContentService = Depends(get_profile_service),
):
    """获取活跃 Profile"""
    content = service.get_active_profile(worker_id)

    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NO_ACTIVE_PROFILE",
                "message": f"No active profile found for worker: {worker_id}"
            }
        )

    return ProfileResponse.from_content(content)


# ============================================================================
# 向量搜索 API
# ============================================================================

class SearchRequest(BaseModel):
    """向量搜索请求（统一接口，支持所有高级功能）"""
    query: str = Field(..., min_length=1, description="搜索查询")
    top_k: int = Field(default=10, ge=1, le=50, description="返回数量")
    mode: str = Field(default="auto", description="模式: auto/legacy/fragment（auto 根据环境变量）")
    min_score: float = Field(default=0.01, ge=0.0, le=1.0, description="最小相似度阈值（0.01 表示接近不过滤）")

    # 高级参数（仅在 Fragment 模式下生效）
    expand_factor: Optional[int] = Field(default=None, ge=1, le=10, description="扩大召回倍数（默认使用全局配置）")
    aggregation_strategy: Optional[str] = Field(default=None, description="聚合策略: best_match/weighted_avg/weighted_best/top3_mean")
    enable_rerank: Optional[bool] = Field(default=None, description="是否启用 Reranker（None=使用全局配置）")
    reranker_model: Optional[str] = Field(default=None, description="Reranker 模型名（None=使用全局配置）")
    filters: Optional[dict[str, Any]] = Field(default=None, description="元数据过滤条件")
    fragment_type_weights: Optional[dict[str, float]] = Field(default=None, description="Fragment 类型权重覆盖，如 {'soul': 3.0, 'skills': 2.0}，未指定类型使用默认权重")


class FragmentMatchInfo(BaseModel):
    """Fragment匹配信息"""
    fragment_type: str
    score: float
    weighted_score: float
    content_preview: str = ""  # 保留兼容
    content: str = ""  # 完整内容


class SearchResultItem(BaseModel):
    """搜索结果项"""
    profile_key: str
    worker_id: str
    profile_id: str
    score: float
    aggregated_score: float | None = None  # Fragment模式的聚合分数
    fragment_matches: list[FragmentMatchInfo] = []  # 命中的fragments详情
    short_profile: str = ""  # 新增：精简画像（30字以内）
    source: str = "vector"


class SearchResponse(BaseModel):
    """搜索响应"""
    query: str
    top_k: int
    mode: str
    results_count: int
    results: list[SearchResultItem]
    timing_ms: float
    config_applied: dict[str, Any] = Field(default_factory=dict, description="实际生效的配置")


def _get_vector_match_service():
    """获取 Vector Match Service"""
    from src.interfaces.api.dependencies.fusion_dependencies import _get_vector_match_service
    return _get_vector_match_service()


def _get_embedding_generator():
    """获取 Embedding Generator"""
    from src.interfaces.api.dependencies.fusion_dependencies import _get_embedding_generator
    return _get_embedding_generator()


def _delete_profile_vectors(worker_id: str, profile_id: str) -> None:
    """
    删除指定 Profile 的所有向量数据

    通过 profile_key 前缀匹配删除该 profile 的所有向量：
    - profile_key 格式: {worker_id}:{profile_id}
    - 向量 ID 格式: {worker_id}:{profile_id}:{fragment_type}:{index}...

    Args:
        worker_id: Worker ID
        profile_id: Profile ID
    """
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import _get_profile_embedding_store

        profile_embedding_store = _get_profile_embedding_store()
        if profile_embedding_store is None:
            logger.warning(f"[ProfileRoutes] Profile embedding store not available, skipping vector deletion")
            return

        profile_key = f"{worker_id}:{profile_id}"
        logger.debug(f"[ProfileRoutes] Looking for vectors with prefix: {profile_key}:")

        # 获取所有向量 ID
        inner_store = profile_embedding_store.vector_store
        logger.debug(f"[ProfileRoutes] Vector store type: {type(inner_store).__name__}")

        all_vector_ids = inner_store.get_vector_ids()
        logger.debug(f"[ProfileRoutes] Total vectors in store: {len(all_vector_ids)}")

        # 筛选以 profile_key: 开头的向量 ID
        prefix = f"{profile_key}:"
        profile_vector_ids = [
            vid for vid in all_vector_ids
            if vid.startswith(prefix)
        ]

        logger.debug(f"[ProfileRoutes] Found {len(profile_vector_ids)} vectors for profile {profile_key}")

        if profile_vector_ids:
            deleted_count = profile_embedding_store.delete_embeddings(profile_vector_ids)
            logger.info(f"[ProfileRoutes] Deleted {deleted_count} vectors for profile {profile_key}")
        else:
            logger.debug(f"[ProfileRoutes] No vectors found for profile {profile_key}")

    except Exception as e:
        # 向量删除失败不应影响主流程，记录错误即可
        logger.error(f"[ProfileRoutes] Failed to delete vectors for profile {worker_id}:{profile_id}: {e}", exc_info=True)


def _rebuild_profile_vectors(worker_id: str, profile_id: str) -> None:
    """
    重建指定 Profile 的向量索引

    用于切换 profile 时重新生成向量索引。

    Args:
        worker_id: Worker ID
        profile_id: Profile ID
    """
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import _build_vector_index_for_worker

        # 使用已有的函数重建向量索引
        # 该函数会扫描所有 profile 并构建索引
        success = _build_vector_index_for_worker(worker_id)
        if success:
            logger.info(f"[ProfileRoutes] Successfully rebuilt vectors for profile {worker_id}:{profile_id}")
        else:
            logger.warning(f"[ProfileRoutes] Failed to rebuild vectors for profile {worker_id}:{profile_id}")

    except Exception as e:
        logger.error(f"[ProfileRoutes] Error rebuilding vectors for profile {worker_id}:{profile_id}: {e}", exc_info=True)


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="向量搜索 Profile",
    description="统一向量搜索接口，支持所有高级功能",
)
async def search_profiles(request: SearchRequest):
    """
    统一向量搜索 Profile

    支持两种模式：
    - legacy: 单向量模式（旧版）
    - fragment: 多 Fragment 模式（新版，默认）

    高级功能（仅 fragment 模式）：
    - expand_factor: 扩大召回倍数
    - aggregation_strategy: 聚合策略
    - enable_rerank + reranker_model: Reranker 精排控制
    - filters: 元数据过滤条件
    """
    import time

    start_time = time.time()
    logger.info("[SEARCH-API] ====== 搜索请求开始 ======")
    logger.info(f"[SEARCH-API] 查询: '{request.query}', top_k={request.top_k}, mode={request.mode}, min_score={request.min_score}")

    # 构建运行时配置
    runtime_config = {}
    if request.expand_factor is not None:
        runtime_config["expand_factor"] = request.expand_factor
    if request.aggregation_strategy is not None:
        runtime_config["aggregation_strategy"] = request.aggregation_strategy

    # 处理 Reranker 配置
    # 获取默认模型（从环境变量，未设置则使用默认值）
    default_reranker_model = os.getenv("RERANKER_MODEL", "bge-reranker-v2-m3")

    if request.enable_rerank is not None:
        if request.enable_rerank:
            # 启用 Reranker，使用传入模型或环境变量默认值
            runtime_config["reranker_model"] = request.reranker_model or default_reranker_model
        else:
            # 禁用 Reranker
            runtime_config["reranker_model"] = None
    elif request.reranker_model is not None:
        # 直接指定了模型，自动启用
        runtime_config["reranker_model"] = request.reranker_model

    if runtime_config:
        logger.info(f"[SEARCH-API] 使用运行时配置: {runtime_config}")

    # 获取服务
    match_service = _get_vector_match_service()
    embedding_gen = _get_embedding_generator()

    if not match_service:
        logger.error("[SEARCH-API] Vector match service 不可用")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "VECTOR_SERVICE_UNAVAILABLE", "message": "Vector match service not available"}
        )

    if not embedding_gen:
        logger.error("[SEARCH-API] Embedding generator 不可用")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "EMBEDDING_UNAVAILABLE", "message": "Embedding service not available"}
        )

    logger.info("[SEARCH-API] 服务获取成功，开始生成 embedding...")

    try:
        # 生成 query embedding
        embed_start = time.time()
        query_embedding = embedding_gen.embed(request.query)
        embed_time = (time.time() - embed_start) * 1000
        logger.info(f"[SEARCH-API] Embedding 生成完成，维度={len(query_embedding)}, 耗时={embed_time:.2f}ms")

        # 设置默认过滤条件：如果未传 filters，默认只搜索 protected 和 public 的 profile
        filters = request.filters
        if filters is None:
            filters = {"availability": ["protected", "public"]}
            logger.info(f"[SEARCH-API] 使用默认过滤条件: {filters}")
        else:
            logger.info(f"[SEARCH-API] 使用用户指定的过滤条件: {filters}")

        # 执行搜索（支持运行时配置和权重覆盖）
        logger.info(f"[SEARCH-API] 开始向量匹配，mode={request.mode}, min_score={request.min_score}")
        if request.fragment_type_weights:
            logger.info(f"[SEARCH-API] 使用运行时 Fragment 权重: {request.fragment_type_weights}")
        match_start = time.time()
        results = match_service.match(
            query_embedding=query_embedding,
            top_k=request.top_k,
            query=request.query,
            mode=request.mode,
            min_score=request.min_score,
            filters=filters,
            runtime_config=runtime_config if runtime_config else None,
            fragment_type_weights=request.fragment_type_weights,
        )
        match_time = (time.time() - match_start) * 1000
        logger.info(f"[SEARCH-API] 向量匹配完成，返回 {len(results)} 条结果，耗时={match_time:.2f}ms")

        if not results:
            logger.warning("[SEARCH-API] ⚠️ 搜索结果为空！请检查：1) 向量索引是否已构建 2) min_score 阈值是否过高 3) Registry 过滤条件")

        # 格式化结果
        formatted_results = []
        for i, result in enumerate(results):
            parts = result.profile_key.split(":")

            # 提取 Fragment 匹配信息
            fragment_matches = []
            if hasattr(result, 'fragment_matches') and result.fragment_matches:
                for fm in result.fragment_matches[:5]:  # 最多返回5个
                    fragment_matches.append(FragmentMatchInfo(
                        fragment_type=getattr(fm, 'fragment_type', 'unknown'),
                        score=getattr(fm, 'score', 0.0),
                        weighted_score=getattr(fm, 'weighted_score', 0.0),
                        content_preview=getattr(fm, 'content_preview', '')[:100],  # 保留预览
                        content=getattr(fm, 'content', '') or getattr(fm, 'content_preview', ''),  # 返回完整内容
                    ))

            # 从 metadata 或 payload 中提取 short_profile
            short_profile = ""
            if hasattr(result, 'metadata') and result.metadata:
                short_profile = getattr(result.metadata, 'short_profile', '')
                logger.info("[SEARCH-API] Result %s: metadata.short_profile='%s'", result.profile_key, short_profile)
                # 如果 metadata 中没有，尝试从 payload 获取
                if not short_profile and hasattr(result.metadata, 'payload'):
                    short_profile = result.metadata.payload.get('short_profile', '')
                    logger.info("[SEARCH-API] Result %s: payload.short_profile='%s'", result.profile_key, short_profile)

            # worker_id 可能包含冒号，所以 profile_id 取最后一部分，worker_id 取前面所有部分
            formatted_results.append(SearchResultItem(
                profile_key=result.profile_key,
                worker_id=":".join(parts[:-1]) if len(parts) > 1 else result.profile_key,
                profile_id=parts[-1] if len(parts) > 1 else "default",
                score=result.score,
                aggregated_score=getattr(result, 'aggregated_score', None),
                fragment_matches=fragment_matches,
                short_profile=short_profile,  # 新增：精简画像
                source="vector",
            ))
            if i < 3:  # 只打印前3个结果的详情
                logger.info(f"[SEARCH-API] 结果[{i+1}]: profile_key={result.profile_key}, score={result.score:.4f}")

        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(f"[SEARCH-API] ====== 搜索请求完成，总耗时={elapsed_ms:.2f}ms，返回 {len(formatted_results)} 条结果 ======")

        # 合并运行时配置和权重配置
        config_applied = dict(runtime_config)
        if request.fragment_type_weights:
            config_applied["fragment_type_weights"] = request.fragment_type_weights

        return SearchResponse(
            query=request.query,
            top_k=request.top_k,
            mode=request.mode,
            results_count=len(formatted_results),
            results=formatted_results,
            timing_ms=round(elapsed_ms, 2),
            config_applied=config_applied,
        )

    except Exception as e:
        logger.error(f"[SEARCH-API] ❌ Search failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SEARCH_ERROR", "message": str(e)}
        )


@router.get(
    "/search/stats",
    summary="搜索统计信息",
    description="获取向量索引的统计信息",
)
async def search_stats():
    """获取搜索/向量索引统计信息"""
    try:
        match_service = _get_vector_match_service()

        if not match_service:
            return {
                "status": "unavailable",
                "message": "Vector match service not available",
                "index_size": 0,
            }

        # 获取索引大小
        vector_size = match_service._vector_store.size() if hasattr(match_service._vector_store, 'size') else 0

        # 获取配置信息
        config = {
            "mode": match_service._config.mode if hasattr(match_service, '_config') else "unknown",
            "enable_fragment": match_service._fragment_config.enable_fragment if hasattr(match_service, '_fragment_config') else False,
            "expand_factor": match_service._fragment_config.expand_factor if hasattr(match_service, '_fragment_config') else 0,
            "aggregation_strategy": match_service._fragment_config.aggregation_strategy if hasattr(match_service, '_fragment_config') else "unknown",
            "reranker_model": match_service._fragment_config.reranker_model if hasattr(match_service, '_fragment_config') else None,
        }

        return {
            "status": "available",
            "index_size": vector_size,
            "config": config,
        }

    except Exception as e:
        logger.error(f"Get stats failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "index_size": 0,
        }


__all__ = ["router"]