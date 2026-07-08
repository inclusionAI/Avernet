"""Bot Render Screen — 业务逻辑层.

只负责 CRUD 语义，不含 HTTP 相关逻辑。
"""
from injector import inject

from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord
from agentclaw.community.core.bot_management.render_screen.repositories import RenderScreenRepository
from agentclaw.community.log import get_logger


logger = get_logger()


class RenderScreenService:
    """第四屏 CDN 配置业务逻辑。"""

    @inject
    def __init__(self, repository: RenderScreenRepository) -> None:
        self._repo = repository

    def list_render_screens(self, *, bot_id: str, owner_id: str) -> list[RenderScreenRecord]:
        """查询某 Bot 下所有 CDN 配置（未删除）。"""
        return self._repo.list_by_bot_id(bot_id=bot_id, owner_id=owner_id)

    def create_render_screen(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        cdn_url: str,
        creator_id: str,
    ) -> int:
        """创建 CDN 配置，返回新记录 id。"""
        existing = self._repo.list_by_bot_id(bot_id=bot_id, owner_id=owner_id)
        if any(r.name == name for r in existing):
            raise ValueError(f"Duplicate name '{name}' for bot_id={bot_id}")
        if any(r.cdn_url == cdn_url for r in existing):
            raise ValueError(f"Duplicate cdn_url '{cdn_url}' for bot_id={bot_id}")
        record_id = self._repo.insert(
            bot_id=bot_id,
            owner_id=owner_id,
            name=name,
            cdn_url=cdn_url,
            creator_id=creator_id,
        )
        logger.info("[RenderScreen] created id=%s bot_id=%s name=%s", record_id, bot_id, name)
        return record_id

    def update_render_screen(
        self,
        *,
        record_id: int,
        name: str,
        cdn_url: str,
    ) -> None:
        """更新 CDN 配置。"""
        existing = self._repo.get_by_id(record_id)
        if existing is None:
            raise ValueError(f"RenderScreen not found: {record_id}")
        self._repo.update_by_id(record_id=record_id, name=name, cdn_url=cdn_url)
        logger.info("[RenderScreen] updated id=%s name=%s", record_id, name)

    def delete_render_screen(self, *, record_id: int) -> None:
        """软删除 CDN 配置。"""
        existing = self._repo.get_by_id(record_id)
        if existing is None:
            raise ValueError(f"RenderScreen not found: {record_id}")
        self._repo.delete_by_id(record_id=record_id)
        logger.info("[RenderScreen] deleted id=%s", record_id)

    def get_render_screen(self, record_id: int) -> RenderScreenRecord | None:
        """查询单条记录。"""
        return self._repo.get_by_id(record_id)
