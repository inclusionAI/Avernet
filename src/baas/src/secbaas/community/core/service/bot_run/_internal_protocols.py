"""Bot 内部 Protocol 定义

定义 BotService、MessageDispatcher、RequestExecutor、SessionLockService
等接口契约，供 core 层内部使用。
与 api/bot_runtime/_protocols.py 中的 BotRunner 等对外 Protocol 不同，
这些是 BotRunner 的内部实现细节，不属于外部 API。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from typing import Any, Protocol, runtime_checkable

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    BotResponse,
    MessageInfo,
    SessionInfo,
)
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.bot_run_queue import BotRunQueueRecord


@runtime_checkable
class PostRunCallback(Protocol):
    """执行完成后触发的回调协议。

     Worker 在 executor 执行完毕后根据 ``record.meta["callback_function"]`` 解析
    回调名称，从 DI 注入的 factories 字典中查找对应的 ``PostRunCallback`` 实例并调用。

     回调接收 ``run_id`` 字符串，可自行查库补充上下文。
    """

    async def __call__(self, run_id: str) -> None: ...


@runtime_checkable
class BotService(Protocol):
    """Bot 服务协议

    定义 Bot 对话服务的标准接口，支持会话生命周期管理。

    设计原则:
    - 当前: 单轮对话，每次 send_message 后 session 即结束
    - 未来: 可扩展为多轮对话，session 保持活跃状态
    """

    async def create_session(
        self,
        *,
        bot_id: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        binding_info: BotBindingInfo | None = None,
        context: BotChatContext | None = None,
        run_id: str | None = None,
    ) -> SessionInfo:
        """创建对话会话

        当前: session 用于单次消息隔离和追踪
        未来: session 可用于维护多轮对话上下文

        Args:
            bot_id: Bot 唯一标识
            session_id: 会话 ID
            metadata: 可选的会话元数据
            binding_info: 可选的已解析 binding 信息（避免重复 DB 查询）
            context: 可选的请求上下文（身份认证、调用者信息等）
            run_id: 可选的运行 ID，用于关联 session 与 run 记录

        Returns:
            SessionInfo: 创建的会话信息

        Raises:
            BotNotFoundError: Bot 不存在
            BotNotAvailableError: Bot 状态不为 ACTIVE
        """
        ...

    async def send_message(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        wait_result: bool = True,
        context: BotChatContext | None = None,
        timeout: int | None = None,
        chat_metadata: dict[str, str] | None = None,
    ) -> BotResponse:
        """发送消息并获取响应

        Args:
            session_id: 会话 ID
            message: 消息内容
            binding_info: 已解析的 binding 信息（用于创建底层连接）
            wait_result: 是否等待结果
            context: 可选的请求上下文（身份认证、调用者信息等）
            timeout: 可选的超时时间（秒），None 表示不限制
            chat_metadata: 可选的 chat 请求元数据，透传给底层实现
        """
        ...

    def send_message_stream(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        timeout: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式发送消息，返回 StreamChunk 迭代器。

        每个 delta/final/error/agent 事件对应一个 StreamChunk。
        流结束时迭代器自然结束（收到 final 或 error chunk 后 stop）。
        """
        ...

    async def inject_message(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> None:
        """注入消息到已有会话

        与 send_message 不同，inject_message 不返回响应结果（返回 None），
        适用于注入系统指令、上下文补充等不需要等待响应的场景。

        Args:
            session_id: 会话 ID
            message: 注入的消息内容
            binding_info: 已解析的 binding 信息（用于创建底层连接）
            context: 可选的请求上下文（身份认证、调用者信息等）
        """
        ...

    async def get_session(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> SessionInfo:
        """查询会话信息（只读）

        通过 session_id 查询会话的元数据，不创建新会话。

        Args:
            session_id: 会话 ID
            binding_info: 已解析的 binding 信息（用于创建底层连接）
            context: 可选的请求上下文（身份认证、调用者信息等）

        Returns:
            SessionInfo: 会话信息，包含 status、created_at、updated_at 等真实数据

        Raises:
            SessionNotFoundError: 会话不存在
        """
        ...

    async def get_messages(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> list[MessageInfo]:
        """获取会话中的消息列表

        Args:
            session_id: 会话 ID
            binding_info: 已解析的 binding 信息（用于创建底层连接）
            context: 可选的请求上下文（身份认证、调用者信息等，用于提取 tenant）

        Returns:
            消息信息列表
        """
        ...


@runtime_checkable
class MessageDispatcher(Protocol):
    """消息分发协议

    定义异步消息执行的接口。BotRunner 将"即发即忘"调度
    委托给 MessageDispatcher 实现，将编排与执行策略解耦。

    实现可以是：
    - TaskMessageDispatcher: asyncio.create_task 后台执行（当前默认）
    - NoopMessageDispatcher: 不执行（测试/占位）
    - MockMessageDispatcher: 记录调用但不执行（单元测试）
    - 未来: 基于消息队列、线程池、进程池等策略

    ``order`` 越大优先级越高，BotRunner 按 order 降序遍历，
    第一个 ``accepts(bot_id)`` 返回 True 的 dispatcher 被选中。
    """

    @property
    def order(self) -> int:
        """优先级，值越大优先级越高。"""
        ...

    def accepts(self, bot_id: str) -> bool:
        """Return True if this dispatcher handles the given bot_id.

        Used by BotRunner to select the appropriate dispatcher from a list.
        Default/dispatcher implementations should return True for all bot_ids
        they are responsible for.
        """
        ...

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
        """分发消息发送以进行异步执行

        此方法必须非阻塞。它安排在后台执行消息发送，
        并立即返回给调用者。

        Args:
            bot_service: BotService 实例
            run_id: 运行 ID
            session_id: 会话 ID
            message: 用户消息
            binding_info: 已解析的绑定信息
            context: 可选的请求上下文
            wait_result: 是否等待结果
            timeout: 可选的超时时间（秒）
            bot_id: 用于队列模式下的每键限制
            callback: 可选的完成回调，签名与
                      asyncio.Task.add_done_callback 一致
            chat_metadata: 可选的 chat 请求元数据，透传给 BotService.send_message
        """
        ...

    def dispatch_send_stream(
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
        """流式消息发送分发，返回 StreamChunk 迭代器。

        实现方决定内部策略：
        - Task 模式：直接透传 bot_service.send_message_stream() 的迭代器
        - Queue 模式：入队后轮询 chunk 表，封装为迭代器返回

        调用方统一 async for 消费，不感知模式差异。
        """
        ...

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
        """分发消息注入以进行异步执行

        此方法必须非阻塞。它安排在后台执行消息注入，
        并立即返回给调用者。

        Args:
            bot_service: BotService 实例
            run_id: 运行 ID
            session_id: 会话 ID
            message: 注入的消息内容
            binding_info: 已解析的绑定信息
            context: 可选的请求上下文
            bot_id: 用于队列模式下的每键限制
        """
        ...


@runtime_checkable
class MachineCountProvider(Protocol):
    """在线 Worker 机器数来源（用于均分 QPM）。"""

    def get_machine_count(self) -> int: ...


@runtime_checkable
class RequestExecutor(Protocol):
    """执行一个已认领（RUNNING）的队列工作项。

    实现方负责：按 ``record.run_id`` 读 ``baas_bot_run`` 取消息/上下文，解析
    binding、建会话、发消息、写最终结果/错误（落 ``baas_bot_run``），以及 session
    串行锁。约定 execute 正常返回即代表该工作项已对应写入终态（COMPLETED/FAILED）
    或已主动放回 PENDING（释放）；execute 不应把异常吞掉后让队列行停在 RUNNING。
    """

    async def execute(self, record: BotRunQueueRecord) -> None: ...


@runtime_checkable
class SessionLockService(Protocol):
    """SerializingExecutor 依赖的最小锁接口（由 DistributedLockService 实现）。"""

    def try_lock(
        self,
        lock_name: str,
        lock_holder: str | None = ...,
        expire_seconds: int | None = ...,
        block: bool = ...,
        block_timeout: float | None = ...,
    ) -> AbstractContextManager[Any]:
        """返回一个上下文管理器，进入后得到带 ``.acquired`` 的锁对象。"""
        ...
