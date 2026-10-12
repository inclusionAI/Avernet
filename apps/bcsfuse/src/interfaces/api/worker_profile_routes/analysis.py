"""Worker/Profile compatibility routes: analysis."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional


logger = logging.getLogger(__name__)

_LLM_ANALYSIS_EXECUTOR: Optional[ThreadPoolExecutor] = None
_LLM_ANALYSIS_EXECUTOR_LOCK = threading.Lock()
_LLM_ANALYSIS_MAX_WORKERS = 4
_profile_analyzer = None


def _get_llm_analysis_executor() -> ThreadPoolExecutor:
    """Return the bounded executor used by background profile analysis."""
    global _LLM_ANALYSIS_EXECUTOR
    if _LLM_ANALYSIS_EXECUTOR is None:
        with _LLM_ANALYSIS_EXECUTOR_LOCK:
            if _LLM_ANALYSIS_EXECUTOR is None:
                _LLM_ANALYSIS_EXECUTOR = ThreadPoolExecutor(
                    max_workers=_LLM_ANALYSIS_MAX_WORKERS,
                    thread_name_prefix="llm-analysis-",
                )
    return _LLM_ANALYSIS_EXECUTOR


def _get_profile_analyzer():
    """获取 Profile Analyzer Service 单例（复用 profile_routes.py 实现）"""
    global _profile_analyzer
    if _profile_analyzer is None:
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import _get_llm_gateway_service
            from src.application.services.profile_analyzer_service import ProfileAnalyzerService

            gateway = _get_llm_gateway_service()
            if gateway:
                try:
                    from src.interfaces.api.dependencies.worker_dependencies import _get_bot_cognition_provider
                    cognition_provider = _get_bot_cognition_provider()
                except Exception:
                    cognition_provider = None
                _profile_analyzer = ProfileAnalyzerService(
                    llm_gateway=gateway,
                    cognition_provider=cognition_provider,
                )
                logger.info("[R3-ProfileAnalyzer] Initialized with LLM Gateway")
            else:
                logger.warning("[R3-ProfileAnalyzer] LLM Gateway not available, analysis disabled")
        except Exception as e:
            logger.warning(f"[R3-ProfileAnalyzer] Init failed: {e}")
    return _profile_analyzer


def _generate_fallback_profile(req, merged_contents: dict) -> str | None:
    """
    从 sync 请求内容生成兜底 profile，确保向量索引有高权重 fragment。

    当调用方未传 contents["profile"] 时，从 soul_md / name / description 提取。
    LLM 分析完成后会替换此兜底值。

    Args:
        req: WorkerSyncRequest
        merged_contents: 已合并的 contents dict

    Returns:
        兜底 profile 文本，或 None
    """
    parts = []
    if req.name:
        parts.append(f"名称: {req.name}")
    if req.description:
        parts.append(f"描述: {req.description}")
    if req.profile.display_name:
        parts.append(f"显示名: {req.profile.display_name}")
    if req.profile.soul_md:
        parts.append(req.profile.soul_md[:2000])

    # 如果都没传，用 merged_contents 的文本值
    if not parts:
        for v in merged_contents.values():
            if isinstance(v, str) and v.strip():
                parts.append(v.strip()[:500])

    return "\n\n".join(parts) if parts else None


async def _analyze_and_persist_async(
    worker_id: str,
    profile_id: str,
    profile_data: dict,
    max_retries: int = 2,
) -> None:
    """
    异步后台任务：LLM 分析 + 写回 profile + 触发向量重建

    对齐内部版 worker_routes.py 的 _analyze_and_persist_async / _analyze_and_persist_sync。
    使用线程池限制并发（最多 4 个线程）。

    Args:
        worker_id: Worker ID
        profile_id: Profile ID
        profile_data: 传入的 profile 数据 dict（含 contents）
        max_retries: 最大重试次数
    """
    import asyncio
    import time

    for attempt in range(max_retries + 1):
        try:
            # Step 1: LLM 分析
            analyzer = _get_profile_analyzer()
            if not analyzer:
                logger.warning(f"[BG-LLM][{worker_id}] Analyzer not available, skip")
                return

            # 构建 WorkerProfileContent 供 analyzer 使用
            from src.domain.models.worker_profile_content import WorkerProfileContent

            contents = profile_data.get("contents", {})
            content_data = dict(profile_data)
            content_data.update({
                "worker_id": worker_id,
                "profile_id": profile_id,
                "soul_md": profile_data.get("soul_md")
                or profile_data.get("content", ""),
            })
            content = WorkerProfileContent.model_validate(content_data)

            logger.info(
                f"[BG-LLM][{worker_id}] Starting analysis (attempt {attempt + 1}/{max_retries + 1})"
            )

            # 在线程池中执行同步 LLM 调用
            loop = asyncio.get_event_loop()
            analysis = await loop.run_in_executor(
                _get_llm_analysis_executor(),
                analyzer.analyze,
                content,
            )

            if not analysis.llm_success:
                logger.warning(
                    f"[BG-LLM][{worker_id}] Analysis failed: {analysis.error_message}"
                )
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return

            # Step 2: 写回 contents
            logger.info(
                f"[BG-LLM][{worker_id}] Writing analysis results, "
                f"tags={analysis.capability_tags}"
            )

            contents["profile"] = analysis.semantic_profile
            contents["capabilities"] = analysis.capability_tags
            contents["short_profile"] = analysis.short_profile

            profile_data["contents"] = contents
            profile_data["updated_at"] = datetime.utcnow().isoformat()

            # 重新获取 profile_store 并更新（后台任务无 request，从 app context 获取）
            from src.interfaces.api.dependencies.fusion_dependencies import (
                get_app_context,
            )
            _ctx = get_app_context()
            profile_store = None
            if _ctx and hasattr(_ctx, 'registry'):
                profile_store = _ctx.registry.get('worker_profile_content_store')
            if profile_store:
                profile_store.upsert_profile(worker_id, profile_id, profile_data)
                logger.info(
                    f"[BG-LLM][{worker_id}] Profile persisted successfully, "
                    f"profile_len={len(analysis.semantic_profile or '')}"
                )

                # Step 3: 触发向量重建（只重算变更的 profile/capabilities fragments）
                try:
                    from src.interfaces.api.dependencies.fusion_dependencies import (
                        _build_vector_index_for_worker,
                    )
                    _build_vector_index_for_worker(worker_id)
                    logger.info(f"[BG-LLM][{worker_id}] Vector rebuild triggered")
                except Exception as e_vec:
                    logger.warning(f"[BG-LLM][{worker_id}] Vector rebuild failed: {e_vec}")

                # 重置服务缓存
                try:
                    from src.interfaces.api.profile_routes import _trigger_index_sync
                    _trigger_index_sync(worker_id)
                except Exception:
                    pass

            return  # 成功完成

        except Exception as e:
            logger.error(
                f"[BG-LLM][{worker_id}] Background task failed "
                f"(attempt {attempt + 1}): {e}",
                exc_info=True,
            )
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
            else:
                logger.error(f"[BG-LLM][{worker_id}] All retries exhausted")
