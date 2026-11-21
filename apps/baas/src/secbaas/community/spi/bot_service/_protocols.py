"""BotService plugin Protocol — bot metadata operations contract."""

from __future__ import annotations

from typing import Protocol

from ._models import (
    BotBindingData,
    CallerExecutionCredential,
    CallerInstanceStatus,
    LogRelationPayload,
)


class BotServicePlugin(Protocol):
    """Plugin protocol for bot metadata operations.

    Currently supports log-relation reporting; future methods for
    fetching/querying bot metadata should be added here.

    Implementations:
    - AiohttpBotServicePlugin: HTTP-based implementation for production.
    - NoopBotServicePlugin: no-op implementation for tests / disabled mode.
    """

    async def report(self, payload: LogRelationPayload) -> None:
        """Report a log-relation record (fire-and-forget).

        Args:
            payload: Log-relation request body.
        """
        ...

    async def get_binding(
        self, bot_id: str, owner_id: str, stage: str, *, default_tag: str | None = None
    ) -> BotBindingData:
        """Query bot binding info from the publish API.

        Args:
            bot_id: Bot identifier.
            owner_id: Owner entity identifier (required query param).
            stage: Lifecycle stage, e.g. ``"online"``, ``"verify"``.
            default_tag: 评测环境 binding 标签（如 ``"default"``、``"eval"``），
                         透传给后端以支持按 default_tag 查询评测 binding。

        Returns:
            BotBindingData with binding details.

        Raises:
            PaasError: On transport failure, HTTP error, or envelope failure.
        """
        ...

    async def get_caller_connection(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        credential: CallerExecutionCredential,
    ) -> str:
        """caller 模式：按 (bot_id, owner_id, user_id) 拉起容器并安装调用方凭据。

        沙箱拉起/轮询是公共逻辑。就绪后按 credential.type 分支：
        ``iam-token`` 单次调用 /api/v1/token/iam?token_type=iam；
        ``dingtalk`` 单次调用 /api/v1/token/iam?token_type=dingtalk；
        ``agentpass`` 通过 BaaS outbound-rule append 安装 ``x-caller-token``。

        Args:
            bot_id: Bot 标识（bare，不含 entity 后缀）。
            owner_id: 容器归属实体。
            user_id: 使用者标识。
            credential: 调用方短时执行凭据；实现内部不得持久化或记录其值。

        Returns:
            新建容器的 sandbox_id。

        Raises:
            PaasError: On transport failure, HTTP error, or envelope failure.
        """
        ...

    async def get_existing_caller_connection(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> str:
        """复用已就绪的 caller 实例：只拉起拿 sandbox_id，不安装任何运行时凭据。

        供 user_id-only 场景使用——调用前应已通过
        :meth:`get_caller_instance_status` 确认实例已登记且存活（实例的执行
        凭据在先前带 token 创建时已安装）。

        Args:
            bot_id: Bot 标识（bare，不含 entity 后缀）。
            owner_id: 容器归属实体。
            user_id: 使用者标识。

        Returns:
            就绪实例的 sandbox_id。

        Raises:
            PaasError: On transport failure, HTTP error, or envelope failure.
        """
        ...

    async def get_caller_instance_status(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> CallerInstanceStatus:
        """只读查询指定用户的独立 Caller 实例登记与存活状态。

        GET /api/v1/expert-chats/app-caller-instance/status：不创建、不恢复、
        不重启实例，不换签、不改账本或 binding。

        Args:
            bot_id: Bot 标识（bare，不含 entity 后缀）。
            owner_id: 容器归属实体。
            user_id: 需要查询独立 Caller 实例的用户 ID。

        Returns:
            CallerInstanceStatus(exists, alive)。

        Raises:
            PaasError: 查询失败（传输、HTTP、信封或契约校验失败）。
                "Caller 不存在/未存活"不是异常，通过返回值表达。
        """
        ...

    async def close(self) -> None:
        """Release underlying resources (HTTP sessions, etc.)."""
        ...
