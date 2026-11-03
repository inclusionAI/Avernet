"""BotService plugin Protocol — bot metadata operations contract."""

from __future__ import annotations

from typing import Protocol

from ._models import BotBindingData, CallerExecutionCredential, LogRelationPayload


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
        ``iam-token`` 继续单次调用 /api/v1/token/iam；``agentpass``
        通过 BaaS outbound-rule append 安装 ``x-caller-token``。

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

    async def close(self) -> None:
        """Release underlying resources (HTTP sessions, etc.)."""
        ...
