"""AC Bot Publish Repository Protocol — 定义对 ac_bot_publish 表的访问接口。"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class AcBotPublishRepository(Protocol):
    """AC Bot Publish Repository Protocol

    定义对 ac_bot_publish 表的访问接口。
    """

    def get_binding_id(
        self,
        *,
        source_bot_id: str,
        status: str = "success",
        owner_id: str | None = None,
        env: str | None = None,
    ) -> int | None: ...

    def get_binding_ids(
        self,
        *,
        source_bot_id: str,
        status: str = "success",
        owner_id: str | None = None,
        env: str | None = None,
    ) -> list[int]:
        """获取所有匹配的 binding_id 列表（对齐 SQL INNER JOIN 行为）。

        与 get_binding_id 的区别：
        1. 返回所有匹配记录的 binding_id（去重），而非只取最新一条
        2. env 参数可选，传入时在 ac_bot_publish 层过滤

        Args:
            source_bot_id: 原始 bot_id
            status: 发布状态（validating/success）
            owner_id: 所有者 ID
            env: 环境参数，可选

        Returns:
            binding_id 列表（按 id DESC 排序，去重）
        """
        ...
