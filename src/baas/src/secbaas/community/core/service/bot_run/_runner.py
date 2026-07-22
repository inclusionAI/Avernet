"""Bot Runner - 单次对话用例编排

封装完整的会话生命周期：resolve → select_service → insert_run → create_session → send/inject
支持 Web、RPC、MCP 等多种入口复用

DB-First 流程：
  1. insert_run (PENDING)   — 先入 DB，保证可追踪
  2. create_session          — 同步等待，返回 session_id
  3. update_session_id       — 持久化 session_id 到 DB
  4. dispatcher.dispatch_*   — 委托异步发送/注入

异步执行委托给 MessageDispatcher：
  - TaskMessageDispatcher: asyncio.create_task 后台执行（默认）
  - NoopMessageDispatcher: 不执行（测试/占位）
  - 未来: 基于消息队列、线程池等策略

并发控制：
  - 可选的 TaskConcurrencyPool 限制全局和 per-bot-id 并发任务数
  - 执行侧的 slot 获取/释放由 TaskMessageDispatcher 管理
    （BotRunner 本身不持有 pool 引用）
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotBindingNotFoundError,
    BotChatContext,
    MessageInfo,
    SessionInfo,
)
from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.bot_run import BotRunRecord, BotRunRepository
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import BotServicePlugin, LogRelationPayload

from ..config import SystemConfigKey
from ._bot_run_utils import (
    binding_data_to_info,
    build_chat_metadata,
    extract_lifecycle_stage,
    extract_session_id_from_record,
    parse_bot_id,
    parse_wait_result,
    resolve_bot_id,
)
from ._bot_service_selector import BotServiceSelector
from ._internal_protocols import BotService, MessageDispatcher

if TYPE_CHECKING:
    from secbaas.community.api.config_manage import SystemConfigManageService

logger = get_logger("core-bot-run")


@dataclass(slots=True)
class _BotRoute:
    """一次请求中解析出的上下文，供多步复用"""

    binding_info: BotBindingInfo
    bot_service: BotService
    route_bot_id: str


class BotRunner:
    """Bot 用例编排器

    提供高阶接口，封装单次对话的完整流程：
    resolve_binding → select_service → insert_run → create_session → dispatch

    DB-First：先入 DB（PENDING），再创建 session，最后委托 dispatcher 异步执行。
    保证即使 create_session 失败，DB 中也有 PENDING → FAILED 记录可追踪。

    并发控制：执行侧的 slot 获取/释放由 MessageDispatcher 实现
    （如 TaskMessageDispatcher）管理。
    """

    def __init__(
        self,
        bot_service_selector: BotServiceSelector,
        run_repository: BotRunRepository,
        bot_service_plugin: BotServicePlugin,
        dispatchers: list[MessageDispatcher],
        system_config_service: SystemConfigManageService | None = None,
        default_request_timeout: float = 30.0,
    ):
        self._bot_service_selector = bot_service_selector
        self._run_repository = run_repository
        self._bot_service_plugin = bot_service_plugin
        self._dispatchers = dispatchers
        self._system_config_service = system_config_service
        self._default_request_timeout = default_request_timeout
        self._dispatcher_map: dict[str, MessageDispatcher] = {
            d.__class__.__name__: d for d in self._dispatchers
        }

    # ── 公开方法 ─────────────────────────────────────────────────────────

    async def chat(
        self,
        *,
        bot_id: str,
        message: str,
        context: BotChatContext,
        metadata: dict[str, Any],
    ) -> str:
        """异步启动单次对话（deliver_message 的简化接口）

        Returns:
            str: run_id，用于后续查询执行结果
        """
        run_id, _ = await self.deliver_message(
            bot_id=bot_id,
            message=message,
            context=context,
            metadata=metadata,
        )
        return run_id

    async def inject_message(
        self,
        *,
        bot_id: str,
        message: str,
        context: BotChatContext,
        metadata: dict[str, Any],
        message_id: str,
    ) -> tuple[str, str]:
        """异步注入消息（不触发推理）

        DB-First：先入 DB，再创建 session，最后委托 dispatcher 异步注入。

        Returns:
            Tuple of (message_id, session_id)
        """
        logger.info(
            "[runner.inject_message] Injecting message: message_id=%s, bot_id=%s, api_key_prefix=%s",
            message_id,
            bot_id,
            context.api_key_prefix,
        )

        # 1. 幂等检查
        existing_run = self._check_idempotency(run_id=message_id)
        if existing_run is not None:
            actual_session_id = extract_session_id_from_record(existing_run)
            if actual_session_id is None:
                actual_session_id = ""
                logger.warning(
                    "[runner.inject_message] actual session id is None, return empty"
                )
            return message_id, actual_session_id

        # 2. DB-first: 入库
        self._insert_run(
            run_id=message_id,
            bot_id=bot_id,
            message=message,
            context=context,
            metadata=metadata,
        )

        route = await self._resolve_bot_route(bot_id, metadata)
        raw_session_id = metadata.get("session_id")

        # 3. 创建会话
        actual_session_id = await self._create_session(
            run_id=message_id,
            session_id=raw_session_id,
            metadata=metadata,
            route=route,
            context=context,
        )

        # 4. 委托 dispatcher 异步注入
        await self._select_dispatcher(bot_id).dispatch_inject(
            bot_service=route.bot_service,
            run_id=message_id,
            session_id=actual_session_id,
            message=message,
            binding_info=route.binding_info,
            context=context,
            bot_id=bot_id,
        )

        chat_metadata = build_chat_metadata(metadata, run_id=message_id)
        # 5. 上报日志关联(后台执行,不阻塞主链路)
        self._fire_and_forget_report(
            run_id=message_id,
            session_id=actual_session_id,
            binding_info=route.binding_info,
            chat_metadata=chat_metadata,
        )

        logger.info(
            "[runner.inject_message] Injected successfully: message_id=%s, bot_id=%s, api_key_prefix=%s, session_id=%s",
            message_id,
            bot_id,
            context.api_key_prefix,
            actual_session_id,
        )
        return message_id, actual_session_id

    async def deliver_message(
        self,
        *,
        bot_id: str,
        message: str,
        context: BotChatContext,
        metadata: dict[str, Any],
        message_id: str | None = None,
        callback: Any = None,
    ) -> tuple[str, str]:
        """异步投递消息

        DB-First 流程：先入 DB（PENDING），再创建 session，
        最后委托 dispatcher 异步发送。

        Returns:
            Tuple of (message_id, session_id)
        """
        timeout: float = float(metadata.get("timeout") or self._default_request_timeout)

        if message_id is None:
            message_id = str(uuid.uuid4())

        logger.info(
            "[runner.deliver_message] Delivering message: message_id=%s, bot_id=%s, api_key_prefix=%s, message=%s",
            message_id,
            bot_id,
            context.api_key_prefix,
            message[:10],
        )

        raw_session_id = metadata.get("session_id")

        # 1. 幂等检查
        existing_run = self._check_idempotency(run_id=message_id)
        if existing_run is not None:
            actual_session_id = extract_session_id_from_record(existing_run)
            if actual_session_id is None:
                actual_session_id = ""
                logger.warning(
                    "[runner.inject_message] actual session id is None, return  empty"
                )
            return message_id, actual_session_id

        # 2. DB-first: 入库
        self._insert_run(
            run_id=message_id,
            bot_id=bot_id,
            message=message,
            context=context,
            metadata=metadata,
        )

        route = await self._resolve_bot_route(bot_id, metadata)

        # 3. 创建会话
        actual_session_id = await self._create_session(
            run_id=message_id,
            session_id=raw_session_id,
            metadata=metadata,
            route=route,
            context=context,
        )

        # 4. 委托 dispatcher 异步发送
        wait_result = parse_wait_result(metadata)
        chat_metadata = build_chat_metadata(metadata, run_id=message_id)
        await self._select_dispatcher(bot_id).dispatch_send(
            bot_service=route.bot_service,
            run_id=message_id,
            session_id=actual_session_id,
            message=message,
            binding_info=route.binding_info,
            context=context,
            wait_result=wait_result,
            timeout=timeout,
            bot_id=bot_id,
            callback=callback,
            chat_metadata=chat_metadata,
        )

        # 5. 上报日志关联(后台执行,不阻塞主链路)
        self._fire_and_forget_report(
            run_id=message_id,
            session_id=actual_session_id,
            binding_info=route.binding_info,
            chat_metadata=chat_metadata,
        )

        logger.info(
            "[runner.deliver_message] Delivered successfully: message_id=%s, bot_id=%s, api_key_prefix=%s, session_id=%s",
            message_id,
            bot_id,
            context.api_key_prefix,
            actual_session_id,
        )
        return message_id, actual_session_id

    async def deliver_message_stream(
        self,
        *,
        bot_id: str,
        message: str,
        context: BotChatContext,
        metadata: dict[str, Any],
        message_id: str | None = None,
    ) -> tuple[str, str, AsyncIterator[StreamChunk]]:
        """流式投递消息

        与 deliver_message 的区别：
        - 不做幂等检查：同一 run_id 已存在则 raise ValueError（→ HTTP 400）
        - 调用 dispatch_send_stream 返回 AsyncIterator[StreamChunk]
        - 不传 callback（流式不支持 PostRunCallback）

        Returns:
            Tuple of (message_id, session_id, AsyncIterator[StreamChunk])
        """
        timeout: float = float(metadata.get("timeout") or self._default_request_timeout)

        if message_id is None:
            message_id = str(uuid.uuid4())

        logger.info(
            "[runner.deliver_message_stream] message_id=%s, bot_id=%s",
            message_id,
            bot_id,
        )

        # 流式模式不做幂等：重复 run_id 直接拒绝
        # 1. 幂等检查
        existing_run = self._check_idempotency(run_id=message_id)
        if existing_run is not None:
            raise ValueError(f"Duplicate request in stream mode: {message_id}")

        route = await self._resolve_bot_route(bot_id, metadata)

        raw_session_id = metadata.get("session_id")

        # DB-first: 入库
        self._insert_run(
            run_id=message_id,
            bot_id=bot_id,
            message=message,
            context=context,
            metadata=metadata,
        )

        # 创建会话
        actual_session_id = await self._create_session(
            run_id=message_id,
            session_id=raw_session_id,
            metadata=metadata,
            route=route,
            context=context,
        )

        # 委托 dispatcher 流式发送
        stream_iter = self._select_dispatcher(
            bot_id, method="stream"
        ).dispatch_send_stream(
            bot_service=route.bot_service,
            run_id=message_id,
            session_id=actual_session_id,
            message=message,
            binding_info=route.binding_info,
            context=context,
            timeout=timeout,
            bot_id=bot_id,
        )

        return message_id, actual_session_id, _with_heartbeat(stream_iter)

    async def get_messages(
        self,
        *,
        bot_id: str,
        context: BotChatContext,
        metadata: dict[str, Any],
        session_id: str | None = None,
    ) -> list[MessageInfo]:
        """获取会话中的消息列表（会自动创建会话）"""
        if session_id is None:
            session_id = metadata.get("session_id")

        route = await self._resolve_bot_route(bot_id, metadata)

        session = await route.bot_service.create_session(
            bot_id=route.route_bot_id,
            session_id=session_id,
            metadata=metadata,
            binding_info=route.binding_info,
            context=context,
        )

        return await route.bot_service.get_messages(
            session_id=session.session_id,
            binding_info=route.binding_info,
            context=context,
        )

    async def get_session_info(
        self,
        *,
        bot_id: str,
        session_id: str,
        context: BotChatContext,
        metadata: dict[str, Any],
    ) -> SessionInfo:
        """查询会话信息（只读）"""
        route = await self._resolve_bot_route(bot_id, metadata)
        return await route.bot_service.get_session(
            session_id=session_id,
            binding_info=route.binding_info,
            context=context,
        )

    async def get_session_messages(
        self,
        *,
        bot_id: str,
        session_id: str,
        context: BotChatContext,
        metadata: dict[str, Any],
    ) -> list[MessageInfo]:
        """获取会话消息列表（只读）"""
        route = await self._resolve_bot_route(bot_id, metadata)
        return await route.bot_service.get_messages(
            session_id=session_id,
            binding_info=route.binding_info,
            context=context,
        )

    def get_result(self, run_id: str) -> Any:
        """获取执行结果

        Raises:
            KeyError: run_id 不存在
        """
        record = self._run_repository.get_by_run_id(run_id)
        if record is None:
            raise KeyError(f"Run not found: {run_id}")
        return record

    # ── 私有方法：步骤提取 ──────────────────────────────────────────────

    async def _report_log_relation(
        self,
        *,
        run_id: str,
        session_id: str,
        binding_info: BotBindingInfo,
        chat_metadata: dict[str, str] | None,
    ) -> None:
        chat_metadata = chat_metadata or {}
        biz_task_id = chat_metadata.get("biz_task_id", run_id)
        biz_scene = chat_metadata.get("biz_scene", "default")
        user_id = binding_info.entity_id
        bot_id = binding_info.bot_id
        engine = binding_info.engine_type
        payload = LogRelationPayload(
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            engine=engine,
            collector="baas",
            refs=[{"ref_type": "session_key", "ref_value": session_id}],
            user_id=user_id,
            bot_id=bot_id,
        )
        logger.info(
            "[runner.report] run_id=%s, bot_id=%s, user_id=%s, engine=%s, "
            "session_id=%s, "
            "biz_scene=%s, biz_task_id=%s",
            run_id,
            bot_id,
            user_id,
            engine,
            session_id,
            biz_scene,
            biz_task_id,
        )
        try:
            await self._bot_service_plugin.report(payload)
        except Exception:
            logger.warning(
                "[runner.report] report failed (non-critical): "
                "run_id=%s, biz_task_id=%s",
                run_id,
                biz_task_id,
                exc_info=True,
            )

    def _fire_and_forget_report(
        self,
        *,
        run_id: str,
        session_id: str,
        binding_info: BotBindingInfo,
        chat_metadata: dict[str, str] | None,
    ) -> None:
        """Fire the log-relation report in the background without blocking the main path.

        log-relation is fire-and-forget side logic: its success/speed has no
        impact on the business flow (the return value is unused, failure only
        logs a WARNING). Running it via create_task keeps a worst-case ~10s HTTP
        request off the response path.

        Normalize session_id: the openclaw engine requires the session_key to be
        prefixed with ``agent:main:`` (mirroring the normalization done when
        ``_claw_service`` creates a session). The prefix is added before
        reporting so downstream log-relation lookups can resolve the session.
        """
        if (
            binding_info.engine_type == "openclaw"
            and session_id
            and not session_id.startswith("agent:main:")
        ):
            session_id = f"agent:main:{session_id}"

        task = asyncio.create_task(
            self._report_log_relation(
                run_id=run_id,
                session_id=session_id,
                binding_info=binding_info,
                chat_metadata=chat_metadata,
            )
        )
        task.add_done_callback(self._on_report_done)

    @staticmethod
    def _on_report_done(task: asyncio.Task[None]) -> None:
        """后台上报任务收尾:吞掉异常,避免 "Task exception was never retrieved"。

        _report_log_relation 内部已捕获并记 WARNING,这里仅作兜底防御。
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "[runner.report] background report task error",
                exc_info=exc,
            )

    def _select_dispatcher(
        self, bot_id: str, *, method: str | None = "chat"
    ) -> MessageDispatcher:
        """根据 system_config 选择 dispatcher。

        查找顺序：``bot_run.dispatcher_route.{bot_id}:{method}`` → ``{bot_id}`` → ``default``。
        值为 dispatcher 类名（如 ``"QueueTaskMessageDispatcher"``），未配置默认走 TaskMessageDispatcher。
        """
        default_name = "TaskMessageDispatcher"
        name = default_name
        if self._system_config_service is not None:
            keys = []
            if method:
                keys.append(f"{SystemConfigKey.DISPATCHER_ROUTE}.{bot_id}:{method}")
            keys.append(f"{SystemConfigKey.DISPATCHER_ROUTE}.{bot_id}")
            keys.append(f"{SystemConfigKey.DISPATCHER_ROUTE}.default")
            for key in keys:
                try:
                    config = self._system_config_service.get_config(key)
                except Exception:
                    logger.warning(
                        "[runner] failed to read dispatcher_route config for key=%s",
                        key,
                        exc_info=True,
                    )
                    continue
                if config is not None:
                    val = (config.conf_value or "").strip()
                    if val:
                        name = val
                        break
        return self._dispatcher_map.get(name, self._dispatchers[-1])

    async def _resolve_bot_route(
        self,
        bot_id: str,
        metadata: dict[str, Any],
    ) -> _BotRoute:
        """解析 binding → 选择 BotService → 解析 bot_id

        Raises:
            BotBindingNotFoundError: binding 不存在
        """
        lifecycle_stage = extract_lifecycle_stage(metadata)
        binding_info = await self._resolve_binding(bot_id, lifecycle_stage)
        if binding_info is None:
            logger.warning(
                "[runner] Bot binding not found: bot_id=%s, lifecycle_stage=%s",
                bot_id,
                lifecycle_stage,
            )
            raise BotBindingNotFoundError(bot_id)

        logger.info(
            "[runner] Bot binding found: bot_id=%s, device_provider=%s",
            bot_id,
            binding_info.device_provider,
        )

        bot_service = self._bot_service_selector.select(binding_info)
        route_bot_id = resolve_bot_id(bot_id, binding_info)
        return _BotRoute(
            binding_info=binding_info,
            bot_service=bot_service,
            route_bot_id=route_bot_id,
        )

    def _check_idempotency(
        self,
        *,
        run_id: str,
    ) -> BotRunRecord | None:
        """幂等检查：若 run_id 已存在则返回记录，否则返回 None。

        Returns:
            BotRunRecord — 幂等命中，调用方应基于此补/取 session 后直接返回；
            None — 未命中，调用方应继续 insert_run + create_session。
        """
        return self._run_repository.get_by_run_id(run_id=run_id)

    def _insert_run(
        self,
        *,
        run_id: str,
        bot_id: str,
        message: str,
        context: BotChatContext,
        metadata: dict[str, Any],
    ) -> None:
        """入库 PENDING 记录（DB-first）"""
        try:
            self._run_repository.insert_run(
                run_id=run_id,
                bot_id=bot_id,
                api_key_prefix=context.api_key_prefix,
                message_long=message,
                metadata=metadata,
            )
        except Exception:
            logger.exception(
                "[runner] Failed to insert run record: run_id=%s, bot_id=%s",
                run_id,
                bot_id,
            )
            raise

    async def _create_session(
        self,
        *,
        run_id: str,
        session_id: str | None,
        metadata: dict[str, Any],
        route: _BotRoute,
        context: BotChatContext,
    ) -> str:
        """创建会话，成功后持久化 session_id；失败时将 run 标记为 FAILED。"""
        try:
            session = await route.bot_service.create_session(
                bot_id=route.route_bot_id,
                session_id=session_id,
                metadata=metadata,
                binding_info=route.binding_info,
                context=context,
                run_id=run_id,
            )
            actual_session_id = session.session_id
            self._run_repository.update_session_id(run_id, actual_session_id)
            return actual_session_id
        except Exception:
            logger.exception(
                "[runner] Session creation failed: run_id=%s, bot_id=%s",
                run_id,
                route.route_bot_id,
            )
            self._run_repository.update_error(
                run_id=run_id, error="Session creation failed"
            )
            raise

    async def _resolve_binding(
        self,
        bot_id: str,
        lifecycle_stage: str = "online",
    ) -> BotBindingInfo | None:
        """解析 bot_id 的 binding 信息（通过 BotServicePlugin）"""
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
                    "[runner] Bot binding unavailable: bot_id=%s, "
                    "lifecycle_stage=%s, error=%s",
                    bot_id,
                    lifecycle_stage,
                    e,
                )
                return None
            raise
        return binding_data_to_info(data)


async def _with_heartbeat(
    stream: AsyncIterator[StreamChunk],
) -> AsyncIterator[StreamChunk]:
    """包装流式迭代器，每 30s 插入一个 heartbeat chunk。

    使用 producer-consumer 模式：后台 task 从原始 stream 取数据放入 Queue，
    主循环从 Queue 取并设超时。这样超时只 cancel queue.get()，不会 kill
    原始 async generator（直接 wait_for(stream.__anext__) 会在超时时 cancel
    生成器，导致后续数据全部丢失）。
    """
    queue: asyncio.Queue[StreamChunk | _StreamEnd | Exception] = asyncio.Queue()
    end = _StreamEnd()

    async def producer() -> None:
        try:
            async for chunk in stream:
                await queue.put(chunk)
        except Exception as e:
            await queue.put(e)
        finally:
            await queue.put(end)

    producer_task = asyncio.create_task(producer())
    try:
        while True:
            try:
                item: StreamChunk | _StreamEnd | Exception = await asyncio.wait_for(
                    queue.get(), timeout=30.0
                )
            except TimeoutError:
                yield StreamChunk(type="heartbeat")
                continue
            if item is end:
                return
            if isinstance(item, Exception):
                raise item
            if isinstance(item, StreamChunk):
                yield item
            else:
                logger.warning(
                    "[heartbeat] Unexpected item type from queue: %s",
                    type(item).__name__,
                )
    finally:
        producer_task.cancel()
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):
            pass


@dataclass
class _StreamEnd:
    """哨兵对象，标记 stream 结束。"""
