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
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.bot_run import BotRunRecord, BotRunRepository
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import BotServicePlugin, LogRelationPayload
from secbaas.community.spi.eval_env import EvalSessionLog

from ..config import SystemConfigKey
from ._binding_resolver import BotBindingResolver
from ._bot_run_utils import (
    build_caller_binding,
    build_chat_metadata,
    extract_session_id_from_record,
    is_caller_mode,
    parse_wait_result,
    plan_session_id,
    resolve_bot_id,
    resolve_user_id,
    strip_sensitive_metadata,
)
from ._bot_service_selector import BotServiceSelector
from ._engine_adapter_registry import BotEngineAdapterRegistry
from ._internal_protocols import BotService, MessageDispatcher

if TYPE_CHECKING:
    from secbaas.community.api.config_manage import SystemConfigManageService
    from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

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
        binding_resolver: BotBindingResolver,
        dispatchers: list[MessageDispatcher],
        system_config_service: SystemConfigManageService,
        eval_session_log: EvalSessionLog,
        default_request_timeout: float = 30.0,
        engine_adapter_registry: BotEngineAdapterRegistry | None = None,
    ):
        self._bot_service_selector = bot_service_selector
        self._run_repository = run_repository
        self._bot_service_plugin = bot_service_plugin
        self._binding_resolver = binding_resolver
        self._dispatchers = dispatchers
        self._system_config_service = system_config_service
        self._default_request_timeout = default_request_timeout
        self._eval_session_log = eval_session_log
        self._engine_adapter_registry = engine_adapter_registry
        self._dispatcher_map: dict[str, MessageDispatcher] = {
            d.__class__.__name__: d for d in self._dispatchers
        }
        # caller 模式后台 dispatch 任务引用：持引用防 GC（asyncio 只持弱引用）
        self._caller_dispatch_tasks: set[asyncio.Task[None]] = set()

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
        attachments: list[Any] | None = None,
    ) -> tuple[str, str]:
        """异步注入消息（不触发推理）

        DB-First：先入 DB，再创建 session，最后委托 dispatcher 异步注入。

        Returns:
            Tuple of (message_id, session_id)
        """
        logger.info(
            "[runner.inject_message] Injecting message: message_id=%s, bot_id=%s, api_key_prefix=%s, metadata=%s",
            message_id,
            bot_id,
            context.api_key_prefix,
            metadata,
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

        # 2. DB-first: 入库（cookie 等敏感凭据剥离——原 metadata 只留内存链路）
        self._insert_run(
            run_id=message_id,
            bot_id=bot_id,
            message=message,
            context=context,
            metadata=strip_sensitive_metadata(metadata),
        )

        # 入库后任一步骤失败都将记录标记为 FAILED 并写入 error，便于排查
        try:
            route = await self._resolve_bot_route(bot_id, metadata)
            self._apply_caller_session_semantics(
                route=route, bot_id=bot_id, metadata=metadata
            )
            raw_session_id = metadata.get("session_id")

            # 3. 创建会话：优先提前构造 session_id，失败则走同步创建
            actual_session_id, session_pending = await self._resolve_session(
                run_id=message_id,
                session_id=raw_session_id,
                metadata=metadata,
                route=route,
                context=context,
            )

            if is_caller_mode(metadata):
                # caller 模式：容器拉起分钟级——dispatch 整体后台化，立即返回
                self._fire_caller_dispatch_later(
                    mode="inject",
                    run_id=message_id,
                    bot_id=bot_id,
                    session_id=actual_session_id,
                    message=message,
                    metadata=metadata,
                    context=context,
                    engine_type=route.binding_info.engine_type,
                    attachments=attachments,
                    session_pending=session_pending,
                )
                logger.info(
                    "[runner.inject_message] caller dispatch deferred: "
                    "message_id=%s, bot_id=%s, session_id=%s",
                    message_id,
                    bot_id,
                    actual_session_id,
                )
                return message_id, actual_session_id

            # 4. 委托 dispatcher 异步注入
            await self._select_dispatcher(
                bot_id, engine_type=route.binding_info.engine_type, metadata=metadata
            ).dispatch_inject(
                bot_service=route.bot_service,
                run_id=message_id,
                session_id=actual_session_id,
                message=message,
                binding_info=route.binding_info,
                context=context,
                bot_id=bot_id,
                attachments=attachments,
                session_pending=session_pending,
            )
        except Exception as e:
            self._mark_run_failed(message_id, e)
            raise

        chat_metadata = build_chat_metadata(
            metadata, run_id=message_id, eval_session_log=self._eval_session_log
        )
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
        attachments: list[Any] | None = None,
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
            "[runner.deliver_message] Delivering message: message_id=%s, bot_id=%s, api_key_prefix=%s, message=%s, metadata=%s",
            message_id,
            bot_id,
            context.api_key_prefix,
            message[:10],
            metadata,
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

        # 2. DB-first: 入库（cookie 等敏感凭据剥离——原 metadata 只留内存链路）
        self._insert_run(
            run_id=message_id,
            bot_id=bot_id,
            message=message,
            context=context,
            metadata=strip_sensitive_metadata(metadata),
        )

        # 入库后任一步骤失败都将记录标记为 FAILED 并写入 error，便于排查
        try:
            route = await self._resolve_bot_route(bot_id, metadata)
            self._apply_caller_session_semantics(
                route=route, bot_id=bot_id, metadata=metadata
            )

            # eval 对话 session 日志与保护性校验 — 委托 Plugin
            eval_id: str | None = metadata.get("eval_id")
            if eval_id:
                logger.info(
                    "[runner.deliver_message] eval chat session: eval_id=%s, bot_id=%s",
                    eval_id,
                    bot_id,
                )
                self._eval_session_log.log_eval_session(
                    eval_id=eval_id,
                    bot_id=bot_id,
                    session_id=metadata.get("session_id", ""),
                    method="deliver_message",
                )
                # Session 保护：评测流量未传显式 session_id，由 BaasBotService 层
                # effective_session_id 兜底（系分 3.3.3）
                session_id_from_metadata = metadata.get("session_id")
                if not session_id_from_metadata:
                    logger.debug(
                        "[runner.deliver_message] eval 对话缺少显式 session_id: "
                        "eval_id=%s, bot_id=%s",
                        eval_id,
                        bot_id,
                    )

            # 3. 创建会话：优先提前构造 session_id，失败则走同步创建
            actual_session_id, session_pending = await self._resolve_session(
                run_id=message_id,
                session_id=raw_session_id,
                metadata=metadata,
                route=route,
                context=context,
            )

            if is_caller_mode(metadata):
                # caller 模式：容器拉起分钟级——dispatch 整体后台化，立即返回
                self._fire_caller_dispatch_later(
                    mode="send",
                    run_id=message_id,
                    bot_id=bot_id,
                    session_id=actual_session_id,
                    message=message,
                    metadata=metadata,
                    context=context,
                    engine_type=route.binding_info.engine_type,
                    callback=callback,
                    timeout=timeout,
                    attachments=attachments,
                    session_pending=session_pending,
                )
                logger.info(
                    "[runner.deliver_message] caller dispatch deferred: "
                    "message_id=%s, bot_id=%s, session_id=%s",
                    message_id,
                    bot_id,
                    actual_session_id,
                )
                return message_id, actual_session_id

            # 4. 委托 dispatcher 异步发送
            wait_result = parse_wait_result(metadata)
            chat_metadata = build_chat_metadata(
                metadata, run_id=message_id, eval_session_log=self._eval_session_log
            )
            await self._select_dispatcher(
                bot_id, engine_type=route.binding_info.engine_type, metadata=metadata
            ).dispatch_send(
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
                attachments=attachments,
                session_pending=session_pending,
            )
        except Exception as e:
            self._mark_run_failed(message_id, e)
            raise

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
        attachments: list[Any] | None = None,
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
            "[runner.deliver_message_stream] message_id=%s, bot_id=%s, metadata=%s",
            message_id,
            bot_id,
            metadata,
        )

        # 流式模式不做幂等：重复 run_id 直接拒绝
        # 1. 幂等检查
        existing_run = self._check_idempotency(run_id=message_id)
        if existing_run is not None:
            raise ValueError(f"Duplicate request in stream mode: {message_id}")

        route = await self._resolve_bot_route(bot_id, metadata)
        self._apply_caller_session_semantics(
            route=route, bot_id=bot_id, metadata=metadata
        )

        # eval 对话 session 日志与保护性校验 — 委托 Plugin
        eval_id: str | None = metadata.get("eval_id")
        if eval_id:
            logger.info(
                "[runner.deliver_message_stream] eval chat session: eval_id=%s, bot_id=%s",
                eval_id,
                bot_id,
            )
            self._eval_session_log.log_eval_session(
                eval_id=eval_id,
                bot_id=bot_id,
                session_id=metadata.get("session_id", ""),
                method="deliver_message_stream",
            )
            # Session 保护：评测流量未传显式 session_id，由 BaasBotService 层
            # effective_session_id 兜底（系分 3.3.3）
            session_id_from_metadata = metadata.get("session_id")
            if not session_id_from_metadata:
                logger.debug(
                    "[runner.deliver_message_stream] eval 对话缺少显式 session_id: "
                    "eval_id=%s, bot_id=%s",
                    eval_id,
                    bot_id,
                )

        raw_session_id = metadata.get("session_id")

        # DB-first: 入库（cookie 等敏感凭据剥离——原 metadata 只留内存链路）
        self._insert_run(
            run_id=message_id,
            bot_id=bot_id,
            message=message,
            context=context,
            metadata=strip_sensitive_metadata(metadata),
        )

        # 入库后任一步骤失败都将记录标记为 FAILED 并写入 error，便于排查
        try:
            # 创建会话：优先提前构造 session_id，失败则走同步创建
            actual_session_id, session_pending = await self._resolve_session(
                run_id=message_id,
                session_id=raw_session_id,
                metadata=metadata,
                route=route,
                context=context,
            )

            if is_caller_mode(metadata):
                # 流式入口无法后台化（stream iterator 须同步返回）——保持同步
                # 拉起：拿到真实 sandbox 后把 route 换成 caller binding 再发送
                caller_binding = await self._binding_resolver.resolve_caller_binding(
                    bot_id=bot_id, metadata=metadata
                )
                caller_binding.engine_type = route.binding_info.engine_type
                route.binding_info = caller_binding
                route.route_bot_id = caller_binding.bot_id
                route.bot_service = self._bot_service_selector.select(caller_binding)

            chat_metadata = build_chat_metadata(
                metadata, run_id=message_id, eval_session_log=self._eval_session_log
            )

            # 委托 dispatcher 流式发送
            stream_iter = self._select_dispatcher(
                bot_id,
                engine_type=route.binding_info.engine_type,
                method="stream",
                metadata=metadata,
            ).dispatch_send_stream(
                bot_service=route.bot_service,
                run_id=message_id,
                session_id=actual_session_id,
                message=message,
                binding_info=route.binding_info,
                context=context,
                timeout=timeout,
                bot_id=bot_id,
                chat_metadata=chat_metadata,
                attachments=attachments,
                session_pending=session_pending,
            )
        except Exception as e:
            self._mark_run_failed(message_id, e)
            raise

        return message_id, actual_session_id, stream_iter

    async def get_messages(
        self,
        *,
        bot_id: str,
        context: BotChatContext,
        metadata: dict[str, Any],
        session_id: str | None = None,
    ) -> list[MessageInfo]:
        """获取会话中的消息列表（会自动创建会话）"""
        logger.info("[runner.get_messages] metadata=%s", metadata)
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
        logger.info("[runner.get_session_info] metadata=%s", metadata)
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
        logger.info("[runner.get_session_messages] metadata=%s", metadata)
        route = await self._resolve_bot_route(bot_id, metadata)
        return await route.bot_service.get_messages(
            session_id=session_id,
            binding_info=route.binding_info,
            context=context,
        )

    async def list_sessions(
        self,
        *,
        bot_id: str,
        context: BotChatContext,
        metadata: dict[str, Any],
        limit: int = 20,
        offset: int = 0,
    ) -> list[SessionInfo]:
        """List sessions for a given bot (read-only)."""
        logger.info("[runner.list_sessions] metadata=%s", metadata)
        route = await self._resolve_bot_route(bot_id, metadata)
        return await route.bot_service.list_sessions(
            binding_info=route.binding_info,
            context=context,
            limit=limit,
            offset=offset,
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

    async def abort(
        self,
        *,
        session_id: str,
        run_id: str | None,
    ) -> None:
        """Best-effort 通知 engine 中止指定 run。

        从 ``baas_bot_run`` 读取 run 记录，解析 bot_id 与 lifecycle_stage，
        重新解析 binding 并选择 BotService，最终调用 ``service.abort`` 发送
        ``chat.abort``。

        Args:
            session_id: 会话 ID（优先使用 run 记录中持久化的实际 session_id）
            run_id: 本次取消的 run ID
        """
        if not run_id:
            logger.warning("[runner.abort] missing run_id, skip engine notify")
            return

        record = self._run_repository.get_by_run_id(run_id)
        if record is None:
            logger.warning(
                "[runner.abort] run not found: run_id=%s session_id=%s",
                run_id,
                session_id,
            )
            return

        actual_session_id = extract_session_id_from_record(record)
        if actual_session_id:
            session_id = actual_session_id

        bot_id = record.bot_id
        metadata = record.metadata or {}

        try:
            binding_info = await self._binding_resolver.resolve_binding(
                bot_id=bot_id, metadata=metadata
            )
        except Exception as e:
            logger.warning(
                "[runner.abort] binding resolution failed: run_id=%s bot_id=%s %s",
                run_id,
                bot_id,
                e,
            )
            return
        if binding_info is None:
            logger.warning(
                "[runner.abort] binding not found: run_id=%s bot_id=%s",
                run_id,
                bot_id,
            )
            return

        bot_service = self._bot_service_selector.select(binding_info)
        try:
            await bot_service.abort(
                session_id=session_id,
                binding_info=binding_info,
            )
            logger.info(
                "[runner.abort] engine abort sent: run_id=%s session_id=%s",
                run_id,
                session_id,
            )
        except Exception:
            logger.warning(
                "[runner.abort] engine abort failed: run_id=%s session_id=%s",
                run_id,
                session_id,
                exc_info=True,
            )

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
        self,
        bot_id: str,
        engine_type: str,
        *,
        metadata: dict[str, Any],
        method: str | None = "chat",
    ) -> MessageDispatcher:
        """根据 system_config 选择 dispatcher。

        查找顺序：``bot_run.dispatcher_route.{bot_id}:{method}`` → ``{bot_id}`` → ``default``。
        值为 dispatcher 类名（如 ``"QueueTaskMessageDispatcher"``），未配置时：
        - BCN 请求（metadata.bot_options.from_bcn == "true"）且
          ``bot_run.bcn_queue_dispatcher_enabled == "true"`` 时默认走 QueueTaskMessageDispatcher
        - 其他请求默认走 TaskMessageDispatcher
        """
        default_name = "TaskMessageDispatcher"
        bot_options: dict[str, Any] | None = metadata.get("bot_options")
        if bot_options is not None and bot_options.get("from_bcn") == "true":
            try:
                flag = self._system_config_service.get_config(
                    SystemConfigKey.BCN_QUEUE_DISPATCHER_ENABLED
                )
                if (
                    flag is not None
                    and (flag.conf_value or "").strip().lower() == "true"
                ):
                    default_name = "QueueTaskMessageDispatcher"
            except Exception:
                logger.warning(
                    "[runner] failed to read bcn_queue_dispatcher_enabled",
                    exc_info=True,
                )

        name = default_name
        keys: list[str] = []
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

    async def _resolve_binding(
        self,
        *,
        bot_id: str,
        lifecycle_stage: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BotBindingInfo | None:
        """统一 binding 解析入口（caller 模式见 resolver.resolve_caller_binding）。

        lifecycle_stage 未显式给出时由 metadata 的 bot_options 提取；
        eval 阶段从 metadata 顶层提取 ``default_tag`` 透传给 get_binding，
        其余阶段不透传。NOT_FOUND 时返回 None。
        """
        return await self._binding_resolver.resolve_binding(
            bot_id=bot_id,
            metadata=metadata or {},
            lifecycle_stage=lifecycle_stage,
        )

    async def _resolve_bot_route(
        self,
        bot_id: str,
        metadata: dict[str, Any],
    ) -> _BotRoute:
        """解析 binding → 选择 BotService → 解析 bot_id

        binding 解析委托 ``BotBindingResolver``（caller 模式见
        ``resolve_caller_binding``）。

        Raises:
            BotBindingNotFoundError: binding 不存在
        """
        binding_info = await self._binding_resolver.resolve_binding(
            bot_id=bot_id, metadata=metadata
        )
        if binding_info is None:
            logger.warning("[runner] Bot binding not found: bot_id=%s", bot_id)
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

    def _apply_caller_session_semantics(
        self,
        *,
        route: _BotRoute,
        bot_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """caller 模式：session 规划改用 caller binding 语义（就地改写 route）。

        与既有 caller 链路语义一致：user_id 走 personal 短路取 entity_id、
        route_bot_id 为 bot_id（``build_caller_binding`` 的默认字段）；唯一升级
        是 engine_type 采用正常解析的真实值——teclaw 走物化，其余复用显式
        session_id。dispatch 用的真实 caller binding（带 sandbox）在拉起后
        另行组装，不经过本方法。
        """
        if not is_caller_mode(metadata):
            return
        session_binding = build_caller_binding(bot_id, "")
        session_binding.engine_type = route.binding_info.engine_type
        route.binding_info = session_binding
        route.route_bot_id = session_binding.bot_id

    def _fire_caller_dispatch_later(
        self,
        *,
        mode: str,
        run_id: str,
        bot_id: str,
        session_id: str,
        message: str,
        metadata: dict[str, Any],
        context: BotChatContext,
        engine_type: str,
        callback: Any = None,
        timeout: float | None = None,
        attachments: list[Any] | None = None,
        session_pending: bool = False,
    ) -> None:
        """caller 模式 dispatch 后台化：拉容器（分钟级）+ IAM 刷新 → 入队。

        cookie 留在任务闭包（内存），落库的 queue meta 只带 sandbox_id；
        失败由任务内部落 run 记录（FAILED），不再上抛。
        """
        task = asyncio.create_task(
            self._caller_dispatch(
                mode=mode,
                run_id=run_id,
                bot_id=bot_id,
                session_id=session_id,
                message=message,
                metadata=metadata,
                context=context,
                engine_type=engine_type,
                callback=callback,
                timeout=timeout,
                attachments=attachments,
                session_pending=session_pending,
            ),
            name=f"caller-dispatch-{run_id}",
        )
        self._caller_dispatch_tasks.add(task)
        task.add_done_callback(self._on_caller_dispatch_done)

    def _on_caller_dispatch_done(self, task: asyncio.Task[None]) -> None:
        """后台任务收尾：释放引用；异常仅记日志（任务内部已兜底落 run）。"""
        self._caller_dispatch_tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()):
            logger.error("[runner] caller dispatch task crashed: %s", exc)

    async def _caller_dispatch(
        self,
        *,
        mode: str,
        run_id: str,
        bot_id: str,
        session_id: str,
        message: str,
        metadata: dict[str, Any],
        context: BotChatContext,
        engine_type: str,
        callback: Any = None,
        timeout: float | None = None,
        attachments: list[Any] | None = None,
        session_pending: bool = False,
    ) -> None:
        """后台执行 caller dispatch：拉容器 → 组真实 caller binding → 入队。"""
        try:
            binding_info = await self._binding_resolver.resolve_caller_binding(
                bot_id=bot_id, metadata=metadata
            )
            binding_info.engine_type = engine_type
            bot_service = self._bot_service_selector.select(binding_info)
            chat_metadata = build_chat_metadata(
                metadata, run_id=run_id, eval_session_log=self._eval_session_log
            )
            dispatcher = self._select_dispatcher(
                bot_id, engine_type=engine_type, metadata=metadata
            )
            if mode == "inject":
                await dispatcher.dispatch_inject(
                    bot_service=bot_service,
                    run_id=run_id,
                    session_id=session_id,
                    message=message,
                    binding_info=binding_info,
                    context=context,
                    bot_id=bot_id,
                    attachments=attachments,
                    session_pending=session_pending,
                )
            else:
                wait_result = parse_wait_result(metadata)
                await dispatcher.dispatch_send(
                    bot_service=bot_service,
                    run_id=run_id,
                    session_id=session_id,
                    message=message,
                    binding_info=binding_info,
                    context=context,
                    wait_result=wait_result,
                    timeout=timeout,
                    bot_id=bot_id,
                    callback=callback,
                    chat_metadata=chat_metadata,
                    attachments=attachments,
                    session_pending=session_pending,
                )
            self._fire_and_forget_report(
                run_id=run_id,
                session_id=session_id,
                binding_info=binding_info,
                chat_metadata=chat_metadata,
            )
            logger.info(
                "[runner] caller dispatch enqueued: run_id=%s, bot_id=%s, "
                "session_id=%s, sandbox_id=%s",
                run_id,
                bot_id,
                session_id,
                binding_info.sandbox_id,
            )
        except Exception as e:
            self._mark_run_failed(run_id, e)
            logger.warning(
                "[runner] caller dispatch failed: run_id=%s, bot_id=%s, %s",
                run_id,
                bot_id,
                e,
            )

    def _adapter_for(self, engine_type: str | None) -> BotEngineAdapter | None:
        """返回 engine_type 对应的已注册 adapter，未注册返回 None。

        registry 未注入（测试/简化装配）时恒 None → plan 走 core 兜底通用格式。
        """
        if (
            engine_type
            and self._engine_adapter_registry is not None
            and self._engine_adapter_registry.has(engine_type)
        ):
            return self._engine_adapter_registry.get(engine_type)
        return None

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

    def _mark_run_failed(self, run_id: str, error: BaseException) -> None:
        """将 run 记录标记为 FAILED 并写入错误信息，便于排查。

        用于 DB-first 流程中「入库之后、dispatch 之前」抛出的异常，避免记录
        一直停留在 PENDING。update_error 幂等（已终态时 no-op）。
        """
        error_msg = f"{type(error).__name__}: {error}"
        try:
            self._run_repository.update_error(run_id=run_id, error=error_msg)
        except Exception:
            # 标记失败本身不能再掩盖原始异常
            logger.exception("[runner] failed to mark run as FAILED: run_id=%s", run_id)

    def _insert_run(
        self,
        *,
        run_id: str,
        bot_id: str,
        message: str,
        context: BotChatContext,
        metadata: dict[str, Any],
    ) -> None:
        """入库 PENDING 记录（DB-first）

        将 context 的 app_id / app_type / tenant 写入 metadata，
        供 Worker 端 ``_rebuild_context`` 重建 BotChatContext。
        """
        metadata["app_id"] = context.app_id
        metadata["app_type"] = context.app_type
        metadata["tenant"] = context.tenant
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

    async def _resolve_session(
        self,
        *,
        run_id: str,
        session_id: str | None,
        metadata: dict[str, Any],
        route: _BotRoute,
        context: BotChatContext,
    ) -> tuple[str, bool]:
        """解析 session_id：复用显式值 > 提前构造计划值。

        非 teclaw 引擎的默认前提：显式传入 session_id 即视为会话已存在，
        直接复用、不物化（与旧 create_session 复用分支语义一致）。
        teclaw 无此前提（引擎侧 sessionKey 语义不同），仍走
        plan/materialize 的 get-or-create。

        Returns:
            (actual_session_id, session_pending)
            session_pending=True 表示 session_id 为计划值，尚未在 adapter 侧物化。
        """
        if session_id is not None and route.binding_info.engine_type != "teclaw":
            self._run_repository.update_session_id(run_id, session_id)
            logger.info(
                "[runner._resolve_session] reuse existing session_id=%s, run_id=%s",
                session_id,
                run_id,
            )
            return session_id, False
        user_id = resolve_user_id(
            metadata, route.binding_info, context, route.route_bot_id
        )
        # teclaw + 显式 session_id 透传（物化时 get-or-create）；
        # 其余到达此处的 session_id 恒为 None
        planned_session_id = (
            session_id
            if session_id is not None
            else plan_session_id(
                tc_bot_id=route.binding_info.bot_id,
                user_id=user_id,
                run_id=run_id,
                eval_id=metadata.get("eval_id"),
                adapter=self._adapter_for(route.binding_info.engine_type),
            )
        )
        self._run_repository.update_session_id(run_id, planned_session_id)
        logger.info(
            "[runner._resolve_session] planned_session_id session_id=%s for run_id=%s",
            planned_session_id,
            run_id,
        )
        return planned_session_id, True
