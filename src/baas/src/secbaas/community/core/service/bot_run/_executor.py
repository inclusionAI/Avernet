"""请求执行器（阶段一，双表）。

执行器链处理 ``baas_bot_run_queue`` 的工作项（``BotRunQueueRecord``），结果正文
落 ``baas_bot_run``（``BotRunRepository``）。链的层次（由外到内）：

- ``SerializingExecutor``（增量 4）：用 ``DistributedLockService`` 给内层执行器
  加 **session 维度串行**。同一 session 同一时刻只有一台机器能持锁执行，不同
  session 并行。串行用现成的 DB 分布式锁（``ac_lock_table`` + ``FOR UPDATE`` +
  后台自动续约），不依赖 ZCache，也不需要把请求路由到固定机器。串行放回 PENDING
  在队列表上完成（``release_to_pending``）。

- ``BotRunRequestExecutor``（增量 5）：按 ``record.run_id`` 读 ``baas_bot_run``
  取消息/上下文，解析 binding、建会话、发/注入消息，写结果回 ``baas_bot_run``。

BCN uplink 逻辑已从执行器链中剥离，改为 ``BcnUplinkCallback``（PostRunCallback），
由 Worker 在执行完毕后根据 ``record.meta["callback_function"]`` 触发。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
)
from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.api_gateway import APIKeyRepository
from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.core.repository.bot_run_queue import BotRunQueueRecord
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import BotServicePlugin

from ._bot_run_utils import (
    binding_data_to_info,
    build_chat_metadata,
    extract_lifecycle_stage,
    parse_bot_id,
    resolve_bot_id,
)
from ._bot_service_selector import BotServiceSelector
from ._internal_protocols import (
    RequestExecutor,
    SessionLockService,
)

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot_run_queue import BotRunQueueRepository
    from secbaas.community.core.repository.bot_run_queue_chunk import (
        BotRunQueueChunkRepository,
    )
    from secbaas.community.spi.cache import CachePlugin


logger = get_logger("core-bot-run")

DEFAULT_SESSION_LOCK_PREFIX = "botrun:session:"
DEFAULT_LOCK_EXPIRE_SECONDS = 300


class RequeuedToPendingError(Exception):
    """Executor 已将工作项放回 PENDING（session 锁被占用），Worker 应跳过
    post_run_callback 和 mark_done，仅释放并发槽位。"""

    def __init__(self, run_id: str, session_id: str) -> None:
        self.run_id = run_id
        self.session_id = session_id
        super().__init__(f"run_id={run_id} requeued, session={session_id} busy")


class SerializingExecutor:
    """给内层 ``RequestExecutor`` 加 session 串行的装饰器。

    - ``record.session_id`` 存在：先抢 ``{prefix}{session_id}`` 的分布式锁，
      抢到才执行内层；抢不到说明同 session 有请求在执行 → 把队列工作项放回
      PENDING，下个 tick 等前序完成后再认领（保证串行且不丢请求）。
    - ``record.session_id`` 为空（一次新会话的首条消息，session 尚未创建）：
      此时无 key 可锁，直接交给内层执行（内层会建会话）。同一新会话"首条消息"
      被并发提交的边界见设计文档 §11，阶段一不在此处兜。
    """

    def __init__(
        self,
        inner: RequestExecutor,
        lock_service: SessionLockService,
        queue_repository: BotRunQueueRepository,
        *,
        lock_prefix: str = DEFAULT_SESSION_LOCK_PREFIX,
        lock_expire_seconds: int = DEFAULT_LOCK_EXPIRE_SECONDS,
    ) -> None:
        self._inner = inner
        self._lock_service = lock_service
        self._queue = queue_repository
        self._lock_prefix = lock_prefix
        self._lock_expire_seconds = lock_expire_seconds

    async def execute(self, record: BotRunQueueRecord) -> None:
        session_id = record.session_id
        if not session_id:
            # 首条消息，尚无 session_id 可锁：直接执行（内层建会话）。
            await self._inner.execute(record)
            return

        lock_name = (
            f"{self._lock_prefix}{record.bot_id}:{record.env or 'dev'}:{session_id}"
        )
        with self._lock_service.try_lock(
            lock_name,
            expire_seconds=self._lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                # 同 session 有请求在执行 → 放回 PENDING，稍后重试，保证串行。
                logger.warning(
                    "[SerializingExecutor] session busy, requeue run_id=%s "
                    "session=%s lock_name=%s bot_id=%s",
                    record.run_id,
                    session_id,
                    lock_name,
                    record.bot_id,
                )
                self._queue.release_to_pending(record.run_id)
                raise RequeuedToPendingError(record.run_id, session_id)
            await self._inner.execute(record)


# ---------------------------------------------------------------------------
# BotRunRequestExecutor — 增量 5
# ---------------------------------------------------------------------------


def _rebuild_context(
    api_key_prefix: str,
    api_key_repository: APIKeyRepository,
) -> BotChatContext:
    """通过 api_key_prefix 查 api_key 记录重建 BotChatContext。

    进入 Worker 后已离开 HTTP 请求生命周期，无法访问 cookie / header。
    参照 BCN 的 _build_chat_context 方式，从 api_key 记录获取
    app_id / app_type / tenant，用 BotChatContext.from_api_key 构造。
    """
    api_key_record = api_key_repository.get_by_prefix(api_key_prefix)
    if not api_key_record:
        raise ValueError(f"api key not found: {api_key_prefix}")
    return BotChatContext.from_api_key(
        api_key_prefix=api_key_record.api_key_prefix,
        app_id=api_key_record.app_id,
        app_type=api_key_record.app_type or "UNKNOWN",
        tenant=api_key_record.tenant or "",
    )


class BotRunRequestExecutor:
    """从 baas_bot_run 重建上下文并执行请求的 RequestExecutor 实现。

    入参是队列工作项 ``BotRunQueueRecord``；按其 ``run_id`` 读 ``baas_bot_run``
    取消息/metadata/api_key，重建 ``BotChatContext``，复用
    ``BotBindingResolver`` 和 ``BotServiceSelector``（与 BotRunner 共享同一实例），
    执行 binding 解析 → 建会话 → 发/注入消息 → 写结果（落 ``baas_bot_run``）。

    设计约束：
    - auth_token / iam_token **不落库**（安全+过期）。当前 ``build_auth_token()``
      仅来自 ``api_key_prefix``（已持久化），不需要额外换取；iam_token 在
      send/inject 路径不被消费，暂留空。
    - session_id 不回写库（``baas_bot_run`` 无此列）：新建会话的 session_id 随
      结果写入 ``result_extra``；首条消息被恢复重跑的串行边界见设计文档 §11。
    """

    def __init__(
        self,
        run_repository: BotRunRepository,
        bot_service_plugin: BotServicePlugin,
        bot_service_selector: BotServiceSelector,
        chunk_repository: BotRunQueueChunkRepository,
        cache_plugin: CachePlugin,
        api_key_repository: APIKeyRepository,
        stream_flush_interval_seconds: float = 0.2,
    ) -> None:
        self._repo = run_repository
        self._bot_service_plugin = bot_service_plugin
        self._bot_service_selector = bot_service_selector
        self._chunk_repository = chunk_repository
        self._cache_plugin = cache_plugin
        self._api_key_repository = api_key_repository
        self._stream_flush_interval = stream_flush_interval_seconds

    async def execute(self, record: BotRunQueueRecord) -> None:
        run = self._repo.get_by_run_id(record.run_id)
        if run is None:
            # 结果行缺失（异常数据）：无处可写，交给 Worker 标记队列 DONE。
            logger.error(
                "[BotRunExecutor] baas_bot_run row missing run_id=%s", record.run_id
            )
            return

        metadata: dict[str, Any] = run.metadata or {}
        request_type = metadata.get("request_type", "chat")
        stream = metadata.get("stream", "false") == "true"
        timeout_sec = metadata.get("timeout")
        chat_metadata = build_chat_metadata(metadata, run.run_id)

        context = _rebuild_context(run.api_key_prefix, self._api_key_repository)
        lifecycle_stage = extract_lifecycle_stage(metadata)
        binding_info = await self._resolve_binding(run.bot_id, lifecycle_stage)

        if binding_info is None:
            self._repo.update_error(run.run_id, f"binding not found: {run.bot_id}")
            return

        # 客户端可见性：进入执行即置 baas_bot_run RUNNING（低频，仅一次）。
        self._repo.update_status(run.run_id, "RUNNING")

        bot_service = self._bot_service_selector.select(binding_info)
        resolved_bot_id = resolve_bot_id(run.bot_id, binding_info)
        session_id = record.session_id
        if not session_id:
            logger.error(
                "[BotRunExecutor] session_id missing for run_id=%s bot_id=%s, "
                "this should not happen (Runner creates session before dispatch)",
                run.run_id,
                run.bot_id,
            )
            self._repo.update_error(run.run_id, "session_id missing")
            return

        logger.info(
            "[BotRunExecutor] start by executor, session_id=%s, resolved_bot_id=%s, run_id=%s, request_type=%s, stream=%s",
            session_id,
            resolved_bot_id,
            run.run_id,
            request_type,
            stream,
        )
        try:
            if request_type == "inject":
                await self._do_inject(
                    run, bot_service, session_id, binding_info, context
                )
            elif stream:
                await self._do_send_stream(
                    run, bot_service, session_id, timeout_sec, binding_info, context
                )
            else:
                await self._do_send(
                    run,
                    bot_service,
                    session_id,
                    metadata,
                    timeout_sec,
                    binding_info,
                    context,
                    chat_metadata,
                )

        except TimeoutError:
            self._repo.update_error(run.run_id, "Task execution timeout")
        except Exception as e:
            logger.exception("[BotRunExecutor] failed run_id=%s: %s", run.run_id, e)
            self._repo.update_error(run.run_id, str(e))

    async def _do_send(
        self,
        run: Any,
        bot_service: Any,
        session_id: str,
        metadata: dict[str, Any],
        timeout_sec: float | None,
        binding_info: BotBindingInfo,
        context: BotChatContext,
        chat_metadata: dict[str, str] | None = None,
    ) -> None:
        wait_result = True
        if "ignore_result" in metadata:
            raw = metadata["ignore_result"]
            ignore = (
                raw
                if isinstance(raw, bool)
                else (
                    str(raw).strip().lower() == "true"
                    if isinstance(raw, str)
                    else bool(raw)
                )
            )
            if ignore:
                wait_result = False

        response = await bot_service.send_message(
            session_id=session_id,
            message=run.message_long or "",
            binding_info=binding_info,
            wait_result=wait_result,
            context=context,
            timeout=timeout_sec,
            chat_metadata=chat_metadata,
        )

        extra: dict[str, Any] = {"session_id": session_id}
        if not wait_result:
            extra["ignore_result"] = "true"
        if response.usage:
            extra["usage"] = {
                "prompt_tokens": response.usage.get("prompt_tokens", 0),
                "completion_tokens": response.usage.get("completion_tokens", 0),
            }

        self._repo.update_result(
            run_id=run.run_id,
            content_long=response.content,
            extra=extra,
        )

    async def _do_send_stream(
        self,
        run: Any,
        bot_service: Any,
        session_id: str,
        timeout_sec: float | None,
        binding_info: BotBindingInfo,
        context: BotChatContext,
    ) -> None:
        """流式发送：消费 bot_service.send_message_stream，逐 chunk 写 chunk 表 + ZCache watermark。

        200ms 批量窗口合并 delta / agent chunks：
        - delta chunks 先缓冲，200ms 或非 delta/agent 事件触发 flush
        - agent chunks 先缓冲到独立 buffer，200ms 或非 delta/agent 事件触发 flush（JSON array 合并）
        - 非 delta/agent（final/error）先 flush 两个缓冲，再立即写入
        - ZCache watermark: cache.set(f"run:{run_id}:seq", f"{seq}:{chunk_type}", ttl=120)
        """

        seq = 0
        delta_buffer: list[str] = []
        delta_engine_type: str | None = None
        agent_buffer: list[dict] = []
        agent_engine_type: str | None = None
        cache_key = f"run:{run.run_id}:seq"

        def _flush_delta() -> None:
            """将缓冲的 delta 合并为一条 chunk 写入。"""
            nonlocal seq, delta_buffer, delta_engine_type
            if not delta_buffer:
                return
            merged = "".join(delta_buffer)
            delta_buffer.clear()
            metadata_json = None
            if delta_engine_type:
                metadata_json = json.dumps({"engine_type": delta_engine_type})
            seq += 1
            self._chunk_repository.insert_chunk(
                run_id=run.run_id,
                seq=seq,
                chunk_type="delta",
                content=merged,
                metadata=metadata_json,
            )
            self._cache_plugin.set(cache_key, f"{seq}:delta", ttl_seconds=120)

        def _flush_agent() -> None:
            """将缓冲的 agent 事件合并为一条 chunk（JSON array）写入。"""
            nonlocal seq, agent_buffer, agent_engine_type
            if not agent_buffer:
                return
            merged = json.dumps(agent_buffer, ensure_ascii=False)
            agent_buffer.clear()
            metadata_json = None
            if agent_engine_type:
                metadata_json = json.dumps({"engine_type": agent_engine_type})
            seq += 1
            self._chunk_repository.insert_chunk(
                run_id=run.run_id,
                seq=seq,
                chunk_type="agent",
                content=merged,
                metadata=metadata_json,
            )
            self._cache_plugin.set(cache_key, f"{seq}:agent", ttl_seconds=120)

        def _flush_buffers() -> None:
            """flush delta 和 agent 两个缓冲。"""
            _flush_delta()
            _flush_agent()

        def _write_chunk(stream_chunk: StreamChunk) -> None:
            """写入非 delta/agent chunk（先 flush 缓冲）。"""
            nonlocal seq
            _flush_buffers()
            seq += 1
            meta = dict(stream_chunk.metadata) if stream_chunk.metadata else {}
            if stream_chunk.engine_type:
                meta["engine_type"] = stream_chunk.engine_type
            metadata_json = json.dumps(meta) if meta else None
            self._chunk_repository.insert_chunk(
                run_id=run.run_id,
                seq=seq,
                chunk_type=stream_chunk.type,
                content=stream_chunk.content,
                metadata=metadata_json,
            )
            self._cache_plugin.set(
                cache_key, f"{seq}:{stream_chunk.type}", ttl_seconds=120
            )

        final_content = ""
        last_flush_ts = time.monotonic()
        try:
            async for chunk in bot_service.send_message_stream(
                session_id=session_id,
                message=run.message_long or "",
                binding_info=binding_info,
                context=context,
                timeout=timeout_sec,
            ):
                if chunk.type == "delta":
                    delta_buffer.append(chunk.content)
                    if chunk.engine_type:
                        delta_engine_type = chunk.engine_type
                    if time.monotonic() - last_flush_ts >= self._stream_flush_interval:
                        _flush_buffers()
                        last_flush_ts = time.monotonic()
                elif chunk.type == "agent":
                    agent_buffer.append(chunk.metadata or {})
                    if chunk.engine_type:
                        agent_engine_type = chunk.engine_type
                    if time.monotonic() - last_flush_ts >= self._stream_flush_interval:
                        _flush_buffers()
                        last_flush_ts = time.monotonic()
                elif chunk.type == "final":
                    final_content = chunk.content
                    _write_chunk(chunk)
                    last_flush_ts = time.monotonic()
                elif chunk.type == "error":
                    _write_chunk(chunk)
                    last_flush_ts = time.monotonic()
                else:
                    logger.debug(
                        "[BotRequestWorker] ignore chunk type: %s, run_id: %s",
                        chunk.type,
                        run.run_id,
                    )
        except Exception:
            _flush_buffers()
            seq += 1
            self._chunk_repository.insert_chunk(
                run_id=run.run_id,
                seq=seq,
                chunk_type="error",
                content="stream execution failed",
            )
            if self._cache_plugin is not None:
                self._cache_plugin.set(cache_key, f"{seq}:error", ttl_seconds=120)
            self._repo.update_error(run.run_id, "stream execution failed")
            return

        # flush 残留 buffer
        _flush_buffers()

        self._repo.update_result(
            run_id=run.run_id,
            content_long=final_content,
            extra={"session_id": session_id, "stream": "true"},
        )

    async def _do_inject(
        self,
        run: Any,
        bot_service: Any,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext,
    ) -> None:
        await bot_service.inject_message(
            session_id=session_id,
            message=run.message_long or "",
            binding_info=binding_info,
            context=context,
        )
        self._repo.update_result(
            run_id=run.run_id,
            content_long="",
            extra={"session_id": session_id, "injected": "true"},
        )

    async def _resolve_binding(
        self, bot_id: str, lifecycle_stage: str
    ) -> BotBindingInfo | None:
        real_bot_id, entity_id = parse_bot_id(bot_id)
        if not real_bot_id:
            return None
        try:
            data = await self._bot_service_plugin.get_binding(
                bot_id=real_bot_id,
                owner_id=entity_id or "",
                stage=lifecycle_stage,
            )
        except PaasError as e:
            if e.code == ErrorCode.NOT_FOUND:
                logger.warning(
                    "[BotRunExecutor] Bot binding unavailable: bot_id=%s, "
                    "lifecycle_stage=%s, error=%s",
                    bot_id,
                    lifecycle_stage,
                    e,
                )
                return None
            raise
        return binding_data_to_info(data)
