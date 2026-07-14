from typing import Protocol, runtime_checkable

from ._record import BotQpmRecord


@runtime_checkable
class BotQpmRepository(Protocol):
    """Bot QPM 配置仓库协议。"""

    def list_all(self) -> list[BotQpmRecord]:
        """列出当前 env 下所有 bot 的 QPM 配置（供 BotQpmManager 全量刷新）。"""
        ...

    def get_by_bot_id(self, bot_id: str) -> BotQpmRecord | None:
        """查询某 bot 的 QPM 配置。"""
        ...

    def upsert(self, *, bot_id: str, qpm: int) -> None:
        """设置/更新某 bot 的 QPM（运营端用）。"""
        ...

    def delete(self, bot_id: str) -> bool:
        """删除某 bot 的 QPM 配置，返回是否删除成功。"""
        ...
