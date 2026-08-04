"""TaskMessageDispatcher — asyncio.create_task 后台执行策略

从 BotRunner 提取的消息发送/注入执行逻辑，通过
asyncio.create_task 在后台 fire-and-forget 执行。

TaskConcurrencyPool 槽位在后台任务内部通过 acquire() 排队获取、
slot.run() / slot.release() 释放。

这是当前行为的直接提取，以便未来可以共存其他策略
（基于消息队列、线程池等）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import BotBindingInfo, BotChatContext
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from ._task_concurrency_pool import TaskConcurrencyPool, TaskConcurrencySlot

logger = get_logger("core-bot-run")


class TaskMessageDispatcher:
    """使用 asyncio.create_task 的消息分发器实现

    当前策略：通过 asyncio.create_task 在 fire-and-forget 的后台任务中执行。
    TaskConcurrencyPool 槽位在任务内部通过 acquire() 排队获取。
    """

    def __init__(
        self,
        run_repository: BotRunRepository,
        task_pool: TaskConcurrencyPool | None = None,
        post_run_callback_factories: dict[str, Any] | None = None,
    ):
        self._run_repository = run_repository
        self._task_pool = task_pool
        self._callback_factories = post_run_callback_factories or {}

    @property
    def order(self) -> int:
        return 0

    def accepts(self, bot_id: str) -> bool:
        """Default dispatcher accepts all bot_ids."""
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
        timeout: float,
        bot_id: str = "",
        callback: Any = None,
        chat_metadata: dict[str, str] | None = None,
        needs_session_creation: bool = False,
    ) -> None:
        task = asyncio.create_task(
            self._execute_send_message(
                bot_service=bot_service,
                run_id=run_id,
                session_id=session_id,
                message=message,
                binding_info=binding_info,
                context=context,
                wait_result=wait_result,
                timeout=timeout,
                bot_id=bot_id,
                chat_metadata=chat_metadata,
                needs_session_creation=needs_session_creation,
            )
        )
        task.add_done_callback(
            self._make_post_run_callback(callback, run_id)
            if callback is not None
            else self._handle_task_exception
        )

    async def dispatch_send_stream(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        timeout: float,
        bot_id: str = "",
        needs_session_creation: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        """流式直传：直接 yield bot_service.send_message_stream 的 chunk。

        与 dispatch_send 不同：
        - 不创建后台 Task，不使用 fire-and-forget
        - 不写 chunk 表，直接透传
        - 在流结束时更新 run 状态（final → update_result, error → update_error）
        - 客户端断连时通过 GeneratorExit 自然终止

        yield:
            StreamChunk: 流式 chunk
        """

        async def _stream_with_status() -> AsyncIterator[StreamChunk]:
            self._run_repository.update_status(run_id, "RUNNING")
            final_content: str = ""
            try:
                if needs_session_creation:
                    await bot_service.create_session(
                        bot_id=bot_id,
                        session_id=session_id,
                        binding_info=binding_info,
                        context=context,
                        run_id=run_id,
                    )
                async for chunk in bot_service.send_message_stream(
                    session_id=session_id,
                    message=message,
                    binding_info=binding_info,
                    context=context,
                    timeout=timeout,
                ):
                    if chunk.type == "final":
                        final_content = chunk.content
                    yield chunk
                # 流正常结束，更新结果
                self._run_repository.update_result(
                    run_id=run_id,
                    content_long=final_content,
                    extra={"session_id": session_id},
                )
            except Exception as e:
                self._run_repository.update_error(
                    run_id=run_id,
                    error=str(e),
                )
                yield StreamChunk(type="error", content=str(e))
            finally:
                # 客户端断连（GeneratorExit）时检查 run 是否已终结，
                # 未终结则标记为 error
                run = self._run_repository.get_by_run_id(run_id)
                if run and run.status not in ("COMPLETED", "FAILED", "TIME_OUT"):
                    self._run_repository.update_error(
                        run_id=run_id,
                        error="stream interrupted",
                    )

        async for chunk in _stream_with_status():
            yield chunk

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
        needs_session_creation: bool = False,
    ) -> None:
        task = asyncio.create_task(
            self._execute_inject_message(
                bot_service=bot_service,
                run_id=run_id,
                session_id=session_id,
                message=message,
                binding_info=binding_info,
                context=context,
                bot_id=bot_id,
                needs_session_creation=needs_session_creation,
            )
        )
        task.add_done_callback(self._handle_task_exception)

    # ── 私有方法 ──────────────────────────────────────────────────────────

    async def _acquire_slot(self, bot_id: str) -> TaskConcurrencySlot | None:
        """为后台任务获取并发槽位

        调用 acquire() 排队等待槽位。无 pool 时返回 None。
        """
        if self._task_pool is None:
            return None
        return await self._task_pool.acquire(key=bot_id)

    async def _execute_send_message(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        wait_result: bool = True,
        timeout: float,
        bot_id: str = "",
        chat_metadata: dict[str, str] | None = None,
        needs_session_creation: bool = False,
    ) -> None:
        """执行消息发送

        当 ``needs_session_creation=True`` 时（session_id 由 runner 本地构造、
        adapter 侧尚未创建会话），先调用 ``bot_service.create_session`` 完成
        WS 解析、baas_bot_session 持久化等副作用，再发送消息。
        """
        slot = await self._acquire_slot(bot_id)
        try:

            async def _do_send() -> None:
                # 1. 更新状态为 RUNNING
                self._run_repository.update_status(run_id, "RUNNING")

                # 2. 懒创建会话（若 session_id 是本地构造的）
                if needs_session_creation:
                    await bot_service.create_session(
                        bot_id=bot_id,
                        session_id=session_id,
                        binding_info=binding_info,
                        context=context,
                        run_id=run_id,
                    )

                # 3. 发送消息
                response = await bot_service.send_message(
                    session_id=session_id,
                    message=message,
                    binding_info=binding_info,
                    wait_result=wait_result,
                    context=context,
                    timeout=max(timeout - 0.2, 0.1),
                    chat_metadata=chat_metadata,
                )

                # 4. 更新成功结果
                extra: dict[str, Any] = {"session_id": session_id}
                if not wait_result:
                    extra["ignore_result"] = "true"
                if response.usage:
                    extra["usage"] = {
                        "prompt_tokens": response.usage.get("prompt_tokens", 0),
                        "completion_tokens": response.usage.get("completion_tokens", 0),
                    }

                self._run_repository.update_result(
                    run_id=run_id,
                    content_long=response.content,
                    extra=extra,
                )

            if slot is not None:
                await slot.run(_do_send(), timeout=timeout)
            else:
                await _do_send()

        except TimeoutError:
            self._run_repository.update_timeout(
                run_id=run_id,
                error="Task execution timeout",
            )
            logger.warning("Send message timed out for run_id=%s", run_id)
        except Exception as e:
            self._run_repository.update_error(
                run_id=run_id,
                error=str(e),
            )
            logger.exception("Send message failed for run_id=%s: %s", run_id, e)

    async def _execute_inject_message(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        bot_id: str = "",
        needs_session_creation: bool = False,
    ) -> None:
        """执行消息注入

        当 ``needs_session_creation=True`` 时先调用 ``bot_service.create_session``
        再注入消息。
        """
        slot = await self._acquire_slot(bot_id)
        try:

            async def _do_inject() -> None:
                # 1. 更新状态为 RUNNING
                self._run_repository.update_status(run_id, "RUNNING")

                # 2. 懒创建会话（若 session_id 是本地构造的）
                if needs_session_creation:
                    await bot_service.create_session(
                        bot_id=bot_id,
                        session_id=session_id,
                        binding_info=binding_info,
                        context=context,
                        run_id=run_id,
                    )

                # 3. 注入消息（不触发推理，无返回值）
                await bot_service.inject_message(
                    session_id=session_id,
                    message=message,
                    binding_info=binding_info,
                    context=context,
                )

                # 4. 更新成功结果（inject 无响应内容）
                self._run_repository.update_result(
                    run_id=run_id,
                    content_long="",
                    extra={"session_id": session_id, "injected": "true"},
                )

            if slot is not None:
                await slot.run(_do_inject())
            else:
                await _do_inject()

        except TimeoutError:
            self._run_repository.update_error(
                run_id=run_id,
                error="Task execution timeout",
            )
            logger.warning("Inject message timed out for run_id=%s", run_id)
        except Exception as e:
            self._run_repository.update_error(
                run_id=run_id,
                error=str(e),
            )
            logger.exception("Inject message failed for run_id=%s: %s", run_id, e)

    def _make_post_run_callback(self, callback: Any, run_id: str) -> Any:
        """构造 PostRunCallback 的 task done callback。

        callback 为字符串时视为注册名，从 DI 注入的 factories 查找实例并异步执行。
        """
        cb_name: str | None = callback if isinstance(callback, str) else None
        factories = self._callback_factories

        def _on_done(task: asyncio.Task[None]) -> None:
            if cb_name and run_id:
                cb_instance = factories.get(cb_name)
                if cb_instance is not None:
                    loop = asyncio.get_running_loop()
                    loop.create_task(cb_instance(run_id))
                else:
                    logger.warning(
                        "[task_dispatcher] callback %r not in factories",
                        cb_name,
                    )

        return _on_done

    def _handle_task_exception(self, task: asyncio.Task[None]) -> None:
        """处理后台任务的异常

        由 asyncio.Task.add_done_callback 调用，捕获并记录未处理异常。
        """
        try:
            task.result()
        except Exception as e:
            logger.exception(f"Background task failed: {e}")
