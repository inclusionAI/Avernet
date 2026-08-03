"""Bot Run Repository Protocol

定义 Bot Run 持久化存储的接口契约。
"""

from typing import Any, Protocol, runtime_checkable

from ._record import BotRunRecord


@runtime_checkable
class BotRunRepository(Protocol):
    """Bot Run Repository Protocol

    定义 Bot Run 持久化存储的接口契约。
    """

    def insert_run(
        self,
        *,
        run_id: str,
        bot_id: str,
        api_key_prefix: str,
        message_long: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        """插入运行记录

        Args:
            run_id: 运行ID (UUID)
            bot_id: Bot ID
            api_key_prefix: API Key 前缀（用于审计）
            message_long: 用户消息（长文本）
            metadata: 可选的元数据

        Returns:
            run_id
        """
        ...

    def get_by_run_id(self, run_id: str) -> BotRunRecord | None:
        """根据 run_id 查询

        Args:
            run_id: 运行ID

        Returns:
            BotRunRecord 或 None
        """
        ...

    def update_status(self, run_id: str, status: str) -> None:
        """更新运行状态

        Args:
            run_id: 运行ID
            status: 新状态 (PENDING/RUNNING/COMPLETED/FAILED)
        """
        ...

    def update_result(
        self,
        run_id: str,
        content_long: str,
        extra: dict[str, Any] | None,
    ) -> None:
        """更新成功结果

        Args:
            run_id: 运行ID
            content_long: Bot 回复内容（长文本）
            extra: 结果额外信息
        """
        ...

    def update_error(self, run_id: str, error: str) -> None:
        """更新失败信息

        Args:
            run_id: 运行ID
            error: 错误信息
        """
        ...

    def update_timeout(self, run_id: str, error: str) -> None:
        """更新超时信息（标记为 TIME_OUT 状态）

        Args:
            run_id: 运行ID
            error: 超时错误信息
        """
        ...

    def update_aborted(self, run_id: str) -> None:
        """中止运行记录（标记为 ABORTED 状态）

        仅允许 PENDING/RUNNING → ABORTED 转移；当 run 已处于终态
        （COMPLETED/FAILED/TIME_OUT/ABORTED）或不存在时，row-affected==0，
        抛出 ``BotRunStatusConflictError``（409）。

        Args:
            run_id: 运行ID

        Raises:
            BotRunStatusConflictError: run 已终态或不存在
        """
        ...

    def update_session_id(self, run_id: str, session_id: str) -> None:
        """更新运行记录的会话 ID

        在 DB-first 流程中，insert_run 在 create_session 之前执行，
        此方法用于在 create_session 成功后补写 session_id。
        将 session_id 合并到 result_extra JSON 中，不覆盖已有字段。

        Args:
            run_id: 运行ID
            session_id: 会话 ID
        """
        ...
