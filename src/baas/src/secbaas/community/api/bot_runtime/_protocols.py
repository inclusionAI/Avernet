"""Bot Service Protocol 定义

定义 Bot 解析服务和 Bot Runner 的接口契约，使用 Python Protocol 实现。

注：BotService / MessageDispatcher / RequestExecutor / SessionLockService
Protocol 已移至 core/service/bot_run/_internal_protocols.py，
UplinkClient Protocol 已移至 core/service/bcn/uplink/_protocol.py，
因为它们是 BotRunner 的内部实现细节，不属于外部 API。
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..bot_manage import BotStartProgressResponse
from ..device_manage import CommandResult
from ._http_connection_info import HttpConnectionInfo
from ._file_transfer_models import (
    GetDownloadUrlResponse,
    GetTransferStatusResponse,
    GetUploadUrlResponse,
)
from ._models import (
    BotChatContext,
    MessageInfo,
    SessionInfo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from secbaas.community.api.sse import StreamChunk
from ._ws_connection_info import WsConnectionInfo


@runtime_checkable
class BotCmdDispatcher(Protocol):
    """命令调度器协议"""

    async def dispatch_bot_execute_command(
        self,
        bot_uuid: str,
        cmd: str,
        tenant: str,
        cmd_env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        device_affinity: str | None = None,
    ) -> CommandResult: ...


@runtime_checkable
class BotHttpDispatcher(Protocol):
    """HTTP 调度器协议"""

    async def dispatch_bot_http_invoke(
        self,
        bot_uuid: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
        tenant: str,
        device_affinity: str | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class BotWssDispatcher(Protocol):
    """WebSocket 调度器协议"""

    async def dispatch_bot_ws_conn_info(
        self,
        bot_uuid: str,
        port: int,
        path: str,
        tenant: str,
        device_affinity: str | None = None,
        device_uuid: str | None = None,
    ) -> WsConnectionInfo: ...


@runtime_checkable
class BotOpenFolderDispatcher(Protocol):
    """Open folder dispatcher protocol — sends open-folder command to a bot's active device.

    Only supported on LOCAL platform. Non-LOCAL platforms raise
    DeviceFacadeException with PLATFORM_ERROR.
    """

    async def dispatch_bot_open_folder(
        self,
        bot_uuid: str,
        tenant: str,
        folder_path: str | None = None,
        device_affinity: str | None = None,
    ) -> bool: ...


@runtime_checkable
class BotFetchStartProgressDispatcher(Protocol):
    """Fetch start progress dispatcher protocol — queries container startup progress.

    Resolves bot_uuid to paas_device_id and fetches the current initialization
    progress from the mng daemon. Returns BotStartProgressResponse with
    current_phase, overall_status, and optional error_message.

    Only supported on LOCAL platform. Non-LOCAL platforms raise
    DeviceFacadeException with PLATFORM_ERROR.
    """

    async def dispatch_bot_fetch_start_progress(
        self,
        bot_uuid: str,
        tenant: str,
        device_affinity: str | None = None,
    ) -> BotStartProgressResponse: ...


@runtime_checkable
class BotFileTransferDispatcher(Protocol):
    """文件传输调度器协议"""

    async def dispatch_get_upload_url(
        self,
        bot_uuid: str,
        tenant: str,
        device_path: str,
        filename: str | None = None,
        expire_seconds: int = 3600,
        staging_subdir: str | None = None,
        device_affinity: str | None = None,
    ) -> GetUploadUrlResponse: ...

    async def dispatch_get_download_url(
        self,
        bot_uuid: str,
        tenant: str,
        device_path: str,
        expire_seconds: int = 3600,
        device_affinity: str | None = None,
    ) -> GetDownloadUrlResponse: ...

    async def dispatch_get_transfer_status(
        self,
        transfer_id: str,
    ) -> GetTransferStatusResponse: ...


@runtime_checkable
class BotHttpConnInfoDispatcher(Protocol):
    """HTTP connection info dispatcher protocol.

    Resolves HTTP connection information (URL + token) for direct
    HTTP access to a bot's active device.
    """

    async def dispatch_bot_http_conn_info(
        self,
        bot_uuid: str,
        port: int,
        path: str,
        tenant: str,
        device_affinity: str | None = None,
        device_uuid: str | None = None,
    ) -> HttpConnectionInfo: ...


@runtime_checkable
class BotRunner(Protocol):
    """Bot Runner 协议

    定义单次对话用例编排器的接口契约，封装完整的会话生命周期：
    resolve → select_service → create_session → send_message

    支持异步执行模式：
    - chat() 异步启动任务，立即返回 run_id
    - get_result(run_id) 用于后续查询执行结果
    """

    async def chat(
        self,
        *,
        bot_id: str,
        message: str,
        context: "BotChatContext",
        metadata: dict[str, Any],
    ) -> str:
        """异步启动单次对话

        先创建会话并返回 run_id，然后异步执行消息发送。

        Args:
            bot_id: Bot 唯一标识，格式为 <real_bot_id>:<entity_id>
            message: 用户消息内容
            context: 请求上下文（身份认证、调用者信息等）
            metadata: 元数据，支持 bot_options.lifecycle_stage 指定生命周期阶段，
                      也可通过 ``timeout`` 键传递超时时间（秒），None 表示不限制

        Returns:
            str: run_id，用于后续查询执行结果

        Raises:
            TooManyRequestsError: reject 策略下并发数超限
        """
        ...

    async def inject_message(
        self,
        *,
        bot_id: str,
        message: str,
        context: "BotChatContext",
        metadata: dict[str, Any],
        message_id: str,
    ) -> tuple[str, str]:
        """异步注入消息（不触发推理）

        创建会话后向其中注入消息，不等待 Bot 响应。
        适用于系统指令注入、上下文补充等场景。

        Args:
            bot_id: Bot 唯一标识，格式为 <real_bot_id>:<entity_id>
            message: 注入的消息内容
            context: 请求上下文
            metadata: 元数据，支持 bot_options.lifecycle_stage 指定生命周期阶段
            message_id: message_id，用于幂等

        Returns:
            Tuple of (message_id, session_id)
        """
        ...

    async def deliver_message(
        self,
        *,
        bot_id: str,
        message: str,
        context: "BotChatContext",
        metadata: dict[str, Any],
        message_id: str | None = None,
        callback: Any = None,
    ) -> tuple[str, str]:
        """异步投递消息

        先创建会话，然后返回 message_id 和 session_id，
        最后异步执行消息发送。

        Args:
            bot_id: Bot 唯一标识，格式为 <real_bot_id>:<entity_id>
            message: 用户消息内容
            context: 请求上下文（身份认证、调用者信息等）
            metadata: 元数据，支持 bot_options.lifecycle_stage 指定生命周期阶段，
                      也可通过 ``timeout`` 键传递超时时间（秒），None 表示不限制
            message_id: 可选的 message_id
            callback: 可选的任务完成回调

        Returns:
            Tuple of (message_id, session_id)
        """
        ...

    async def deliver_message_stream(
        self,
        *,
        bot_id: str,
        message: str,
        context: "BotChatContext",
        metadata: dict[str, Any],
        message_id: str | None = None,
    ) -> tuple[str, str, "AsyncIterator[StreamChunk]"]:
        """流式投递消息

        与 deliver_message 的区别：
        - 不做幂等检查：同一 run_id 已存在则 raise ValueError（→ HTTP 400）
        - 返回 AsyncIterator[StreamChunk] 供调用方消费流式 chunk

        Args:
            bot_id: Bot 唯一标识，格式为 <real_bot_id>:<entity_id>
            message: 用户消息内容
            context: 请求上下文（身份认证、调用者信息等）
            metadata: 元数据，支持 bot_options.lifecycle_stage 指定生命周期阶段，
                      也可通过 ``timeout`` 键传递超时时间（秒），None 表示不限制
            message_id: 可选的 message_id

        Returns:
            Tuple of (message_id, session_id, AsyncIterator[StreamChunk])

        Raises:
            ValueError: 重复 run_id（流式模式不做幂等）
        """
        ...

    async def get_messages(
        self,
        *,
        bot_id: str,
        context: BotChatContext,
        metadata: dict[str, Any],
        session_id: str | None = None,
    ) -> list[MessageInfo]:
        """获取会话中的消息列表（会自动创建会话）

        注意：此方法会在会话不存在时创建新会话。如需只读查询，请使用 get_session_messages。

        Args:
            bot_id: Bot 唯一标识，格式为 <real_bot_id>:<entity_id>
            context: 请求上下文
            metadata: 元数据，支持 bot_options.lifecycle_stage 指定生命周期阶段
            session_id: 可选的会话 ID，若 metadata 中有 session_id 也可从中获取

        Returns:
            消息信息列表
        """
        ...

    async def get_session_info(
        self,
        *,
        bot_id: str,
        session_id: str,
        context: "BotChatContext",
        metadata: dict[str, Any],
    ) -> "SessionInfo":
        """查询会话信息（只读）

        通过 session_id 查询会话的元数据，不创建新会话。
        如果会话不存在，抛出 SessionNotFoundError。

        Args:
            bot_id: Bot 唯一标识，格式为 <real_bot_id>:<entity_id>
            session_id: 会话 ID
            context: 请求上下文
            metadata: 元数据，支持 bot_options.lifecycle_stage 指定生命周期阶段

        Returns:
            SessionInfo: 会话信息

        Raises:
            SessionNotFoundError: 会话不存在
        """
        ...

    async def get_session_messages(
        self,
        *,
        bot_id: str,
        session_id: str,
        context: "BotChatContext",
        metadata: dict[str, Any],
    ) -> list["MessageInfo"]:
        """获取会话消息列表（只读）

        直接查询指定会话的消息，不创建新会话。

        Args:
            bot_id: Bot 唯一标识，格式为 <real_bot_id>:<entity_id>
            session_id: 会话 ID
            context: 请求上下文
            metadata: 元数据，支持 bot_options.lifecycle_stage 指定生命周期阶段

        Returns:
            消息信息列表
        """
        ...

    def get_result(self, run_id: str) -> Any:
        """获取执行结果

        Args:
            run_id: 由 chat() 返回的运行 ID

        Returns:
            BotRunRecord: 包含执行状态和结果的运行记录

        Raises:
            KeyError: run_id 不存在
        """
        ...
