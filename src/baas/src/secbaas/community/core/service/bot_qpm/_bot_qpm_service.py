"""Bot QPM 配置管理 service 实现。

封装 bot_qpm_config 表的 CRUD 业务逻辑，router 层通过此 service 访问数据。
"""

from __future__ import annotations

from secbaas.community.api.bot_qpm import (
    BotQpmConfigItem,
    BotQpmConfigListResult,
    BotQpmManageService,
)
from secbaas.community.core.repository.bot_qpm import BotQpmRecord, BotQpmRepository
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


def _to_item(record: BotQpmRecord) -> BotQpmConfigItem:
    return BotQpmConfigItem(
        id=record.id,
        bot_id=record.bot_id,
        qpm=record.qpm,
        env=record.env,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


class DefaultBotQpmManageService(BotQpmManageService):
    """Bot QPM 配置管理 service 实现。"""

    def __init__(self, repository: BotQpmRepository) -> None:
        self._repository = repository

    def list_configs(self) -> BotQpmConfigListResult:
        records = self._repository.list_all()
        items = [_to_item(r) for r in records]
        logger.info("[bot-qpm:list] returned %s configs", len(items))
        return BotQpmConfigListResult(items=items, total=len(items))

    def get_config(self, bot_id: str) -> BotQpmConfigItem | None:
        record = self._repository.get_by_bot_id(bot_id)
        if record is None:
            return None
        return _to_item(record)

    def upsert_config(self, *, bot_id: str, qpm: int) -> BotQpmConfigItem:
        self._repository.upsert(bot_id=bot_id, qpm=qpm)
        record = self._repository.get_by_bot_id(bot_id)
        assert record is not None, f"Record not found after upsert: bot_id={bot_id}"
        logger.info("[bot-qpm:upsert] bot_id=%s qpm=%s", bot_id, qpm)
        return _to_item(record)

    def update_config(self, *, bot_id: str, qpm: int) -> BotQpmConfigItem | None:
        existing = self._repository.get_by_bot_id(bot_id)
        if existing is None:
            return None
        self._repository.upsert(bot_id=bot_id, qpm=qpm)
        record = self._repository.get_by_bot_id(bot_id)
        assert record is not None, f"Record not found after update: bot_id={bot_id}"
        logger.info("[bot-qpm:update] bot_id=%s qpm=%s", bot_id, qpm)
        return _to_item(record)

    def delete_config(self, bot_id: str) -> bool:
        success = self._repository.delete(bot_id)
        if success:
            logger.info("[bot-qpm:delete] bot_id=%s", bot_id)
        return success
