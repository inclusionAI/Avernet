"""QueueTaskMessageDispatcher — 队列化消息分发策略

实现 MessageDispatcher 协议，将消息投递改为队列化：
dispatch_send / dispatch_inject 只做「写结果行 + 写队列工作项」两步入库，
不即时执行，由 Worker 从队列捞取后异步执行。

与 TaskMessageDispatcher（asyncio.create_task 即时执行）对比：
- TaskMessageDispatcher: fire-and-forget，当前进程内执行
- QueueTaskMessageDispatcher: 只入库，Worker 拉取后执行

背压保护：
- 可选的 per-bot PENDING 深度阈值（max_queue_depth），超限抛 TooManyRequestsError(429)

幂等由 BotRunner._check_idempotency 保证，dispatcher 层不做重复检查。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    TooManyRequestsError,
)
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.core.service.config import SystemConfigKey
from secbaas.community.logger import get_logger
from secbaas.community.tracer import get_tracer_plugin

if TYPE_CHECKING:
    from secbaas.community.api.config_manage import SystemConfigManageService
    from secbaas.community.core.repository.bot_run_queue import BotRunQueueRepository
    from secbaas.community.core.repository.bot_run_queue_chunk import (
        BotRunQueueChunkRepository,
    )
    from secbaas.community.spi.cache import CachePlugin

logger = get_logger("core-bot-run")


class QueueTaskMessageDispatcher:
    """队列化消息分发器

    实现 MessageDispatcher 协议：
    - dispatch_send: 写 PENDING 结果行 + 队列工作项，立即返回
    - dispatch_inject: 同上，request_type 标记为 inject
    """

    def __init__(
        self,
        run_repository: BotRunRepository,
        queue_repository: BotRunQueueRepository,
        chunk_repository: BotRunQueueChunkRepository,
        cache_plugin: CachePlugin,
        max_queue_depth: int = 0,
        system_config_service: SystemConfigManageService | None = None,
    ):
        self._run_repository = run_repository
        self._queue_repository = queue_repository
        self._max_queue_depth = max_queue_depth
        self._chunk_repository = chunk_repository
        self._cache_plugin = cache_plugin
        self._system_config_service = system_config_service

    @property
    def order(self) -> int:
        return 100

    def accepts(self, bot_id: str) -> bool:
        return True

    async def dispatch_send(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        wait_result: bool = True,
        timeout: int | None = None,
        bot_id: str = "",
        callback: Any = None,
        chat_metadata: dict[str, str] | None = None,
    ) -> None:
        """队列化消息发送：只入库（PENDING），Worker 异步执行。

        bot_service / binding_info 等参数不在此处使用（Worker 从 DB 重建上下文），
        但保留在签名中以兼容 MessageDispatcher 协议。
        """
        self._check_backpressure(bot_id)
        meta: dict[str, Any] = {
            "request_type": "chat",
            "bot_options": {"lifecycle_stage": "all"},
        }
        if isinstance(callback, str):
            meta["callback_function"] = callback
        if timeout is not None:
            meta["timeout"] = timeout
        self._enqueue_work(run_id, bot_id, session_id, meta=meta)
        logger.info(
            "[queue_dispatcher.dispatch_send] run_id=%s bot_id=%s session_id=%s",
            run_id,
            bot_id,
            session_id,
        )

    async def dispatch_inject(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        bot_id: str = "",
    ) -> None:
        """队列化消息注入：只入库（PENDING），Worker 异步执行。

        inject 不触发推理但需与同 session 的 send 串行，
        串行由 Worker 端 DistributedLockService 的 session 锁保证。
        """
        self._check_backpressure(bot_id)
        meta: dict[str, Any] = {"request_type": "inject"}
        self._enqueue_work(run_id, bot_id, session_id, meta=meta)
        logger.info(
            "[queue_dispatcher.dispatch_inject] run_id=%s bot_id=%s session_id=%s",
            run_id,
            bot_id,
            session_id,
        )

    # ── 私有方法 ──────────────────────────────────────────────────────────

    async def dispatch_send_stream(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        timeout: int | None = None,
        bot_id: str = "",
    ) -> AsyncIterator[StreamChunk]:
        """队列化流式发送：入队 + 轮询 chunk 表。

        1. 入队（meta 标记 stream=true），Worker 执行 _do_send_stream 写 chunk
        2. 轮询 chunk 表，逐 chunk 产出
        3. 遇到 final/error chunk 后停止
        4. finally 清理 chunk 表记录

        yield:
            StreamChunk: 流式 chunk
        """
        self._check_backpressure(bot_id)
        meta: dict[str, Any] = {"request_type": "chat", "stream": "true"}
        if timeout is not None:
            meta["timeout"] = timeout
        self._enqueue_work(run_id, bot_id, session_id, meta=meta)

        logger.info(
            "[queue_dispatcher.dispatch_send_stream] run_id=%s bot_id=%s",
            run_id,
            bot_id,
        )

        async for chunk in self._poll_chunks(run_id, timeout=timeout):
            yield chunk

    async def _poll_chunks(
        self,
        run_id: str,
        *,
        timeout: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """轮询 chunk 表消费流式数据。

        使用 ZCache watermark 避免空轮询：
        - 读取 cache key `run:{run_id}:seq` 获取最新 seq 和 chunk_type
        - 仅当 seq 变化时查 DB 取 chunk 内容

        遇到 final/error chunk 后停止迭代。
        finally 块清理 chunk 表记录。
        """
        terminal_types = {"final", "error"}
        last_seq = 0
        cache_key = f"run:{run_id}:seq"
        deadline = asyncio.get_event_loop().time() + timeout if timeout else None

        try:
            while True:
                if deadline is not None:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        logger.warning(
                            "[queue_dispatcher] total timeout exceeded for run_id=%s",
                            run_id,
                        )
                        yield StreamChunk(type="error", content="stream timeout")
                        return
                # 1. 读 watermark（cheap，避免每次都查 DB）
                watermark: str | None = None
                try:
                    raw = self._cache_plugin.get(cache_key)
                    if raw is not None:
                        watermark = str(raw)
                except Exception:
                    pass

                # 2. 解析 watermark: "seq:chunk_type"
                wm_seq = 0
                if watermark:
                    parts = watermark.split(":", 1)
                    try:
                        wm_seq = int(parts[0])
                    except ValueError:
                        pass

                # 3. seq 有变化 → 查 DB 取新 chunks
                if wm_seq > last_seq:
                    chunks = self._chunk_repository.get_chunks_after(run_id, last_seq)
                    for chunk_rec in chunks:
                        last_seq = chunk_rec.seq
                        if chunk_rec.chunk_type == "agent":
                            # agent chunk 是合并的 JSON array，拆开还原为多个 StreamChunk
                            try:
                                frames = json.loads(chunk_rec.content or "[]")
                            except (json.JSONDecodeError, TypeError):
                                frames = []
                            engine_type = None
                            if chunk_rec.metadata:
                                try:
                                    meta = json.loads(chunk_rec.metadata)
                                    if isinstance(meta, dict):
                                        engine_type = meta.get("engine_type")
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            for frame in frames:
                                yield StreamChunk(
                                    type="agent",
                                    content="",
                                    metadata=frame,
                                    engine_type=engine_type,
                                )
                        else:
                            metadata = None
                            engine_type = None
                            if chunk_rec.metadata:
                                try:
                                    metadata = json.loads(chunk_rec.metadata)
                                    if isinstance(metadata, dict):
                                        engine_type = metadata.pop("engine_type", None)
                                        if not metadata:
                                            metadata = None
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            yield StreamChunk(
                                type=chunk_rec.chunk_type,
                                content=chunk_rec.content or "",
                                metadata=metadata,
                                engine_type=engine_type,
                            )
                        if chunk_rec.chunk_type in terminal_types:
                            return
                else:
                    # seq 没变，短暂等待避免 busy-loop
                    await asyncio.sleep(0.05)

                # 4. 检查 run 是否已终结（Worker 可能已崩溃）
                run = self._run_repository.get_by_run_id(run_id)
                if run and run.status in ("FAILED", "TIME_OUT"):
                    yield StreamChunk(
                        type="error",
                        content=f"run terminated with status {run.status}",
                    )
                    return

        finally:
            if self._should_cleanup_chunks():
                self._cleanup_chunks(run_id)

    def _should_cleanup_chunks(self) -> bool:
        """是否在流结束后清理 chunk 记录。

        从 system_config 读取 ``bot_run.chunk_cleanup_enabled`` 配置：
        - 未配置或值为 ``"true"``：执行清理（默认行为）
        - 值为 ``"false"``：跳过清理
        """

        if self._system_config_service is None:
            return True
        try:
            config = self._system_config_service.get_config(
                SystemConfigKey.CHUNK_CLEANUP_ENABLED
            )
        except Exception:
            logger.warning(
                "[queue_dispatcher] failed to read chunk cleanup config, "
                "defaulting to cleanup enabled",
                exc_info=True,
            )
            return False
        if config is None:
            return False
        return (config.conf_value or "").strip().lower() == "true"

    def _cleanup_chunks(self, run_id: str) -> None:
        """清理 chunk 表中该 run 的所有记录。"""
        try:
            self._chunk_repository.delete_chunks_by_run(run_id)
        except Exception:
            logger.warning(
                "[queue_dispatcher] cleanup chunks failed for run_id=%s",
                run_id,
                exc_info=True,
            )

    def _check_backpressure(self, bot_id: str) -> None:
        """队列化入口背压：per-bot PENDING 深度超阈值则拒绝（429）。

        阈值 <=0 时直接放行。深度查询失败不应阻断入队（背压是
        尽力而为的保护，不是正确性必需），记录告警后放行。
        """
        if self._max_queue_depth <= 0:
            return
        try:
            depth = self._queue_repository.count_pending_by_bot(bot_id)
        except Exception as e:
            logger.warning(
                "[queue_dispatcher] backpressure depth query failed bot_id=%s err=%s",
                bot_id,
                e,
            )
            return
        if depth >= self._max_queue_depth:
            raise TooManyRequestsError(
                bot_id=bot_id, active=depth, limit=self._max_queue_depth
            )

    def _enqueue_work(
        self,
        run_id: str,
        bot_id: str,
        session_id: str | None,
        *,
        meta: dict | None = None,
    ) -> None:
        """把队列工作项写入 ``baas_bot_run_queue``。"""
        trace_carrier: dict[str, str] = {}
        get_tracer_plugin().inject_context(trace_carrier)
        if trace_carrier:
            if meta is None:
                meta = {}
            meta["traceparent"] = trace_carrier
        self._queue_repository.insert_queue(
            run_id=run_id, bot_id=bot_id, session_id=session_id, meta=meta
        )

    @staticmethod
    def _build_metadata(
        context: BotChatContext | None,
        *,
        session_id: str | None = None,
        request_type: str = "chat",
    ) -> dict[str, Any]:
        """构建 Worker 重建上下文所需的 metadata。

        以下字段由 Worker 端 ``BotRunRequestExecutor`` 读取来重建 ``BotChatContext``：
        - ``app_id`` / ``app_type`` / ``tenant``：来自 BotChatContext
        - ``session_id``：会话 ID
        - ``request_type``：chat / inject
        """
        metadata: dict[str, Any] = {}
        if session_id:
            metadata["session_id"] = session_id
        if context:
            metadata.setdefault("app_id", context.app_id)
            metadata.setdefault("app_type", context.app_type)
            metadata.setdefault("tenant", context.tenant)
        metadata["request_type"] = request_type
        return metadata
