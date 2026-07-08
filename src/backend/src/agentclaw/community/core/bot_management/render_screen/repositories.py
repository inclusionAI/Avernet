"""Bot Render Screen — Repository Protocol.

定义 render screen 持久化操作接口。
实现位于 plugins/local 和 plugins/prod。
"""
from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord


@runtime_checkable
class RenderScreenRepository(Protocol):
    """Render screen repository interface.

    Implementation: a single unified ORM repository
    ``plugins.render_screen_repository.RenderScreenRepository`` that
    runs on both the corp store (prod) and SQLite (local) — the only difference is
    the injected ``DatabasePlugin`` (``orm_session()``).
    """

    def insert(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        cdn_url: str,
        creator_id: str,
    ) -> int:
        """插入一条 CDN 配置，返回新记录 id。"""
        ...

    def list_by_bot_id(self, *, bot_id: str, owner_id: str) -> list[RenderScreenRecord]:
        """查询某 Bot 下所有未删除的 CDN 配置。"""
        ...

    def get_by_id(self, record_id: int) -> RenderScreenRecord | None:
        """根据 id 查询单条记录。"""
        ...

    def update_by_id(
        self,
        *,
        record_id: int,
        name: str,
        cdn_url: str,
    ) -> None:
        """更新 name 和 cdn_url。"""
        ...

    def delete_by_id(self, *, record_id: int) -> None:
        """软删除（is_delete=1）。"""
        ...
