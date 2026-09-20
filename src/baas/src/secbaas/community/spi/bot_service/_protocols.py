"""BotService plugin Protocol — bot metadata operations contract."""

from __future__ import annotations

from typing import Protocol

from ._models import BotBindingData, LogRelationPayload


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
        cookie: str,
    ) -> str:
        """caller 模式：按 (bot_id, owner_id, user_id) 拉起一个新容器，返回 sandbox_id。

        依赖外部 caller-connection 接口；Principal 不经参数传入——real 实现用
        共享密钥（经 SecretStorePlugin）自行签发 app principal 并置于
        ``X-Avernet-Principal``。实例就绪后用 ``cookie`` 单次调用
        /api/v1/token/iam 刷新 Caller 执行凭据（副作用在服务端完成）。

        Args:
            bot_id: Bot 标识（bare，不含 entity 后缀）。
            owner_id: 容器归属实体。
            user_id: 使用者标识。
            cookie: 调用方登录态 Cookie（含 IAM_TOKEN），就绪后用于刷新凭据。

        Returns:
            新建容器的 sandbox_id。

        Raises:
            PaasError: On transport failure, HTTP error, or envelope failure.
        """
        ...

    async def close(self) -> None:
        """Release underlying resources (HTTP sessions, etc.)."""
        ...
