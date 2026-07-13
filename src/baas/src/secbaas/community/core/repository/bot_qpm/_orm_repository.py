"""ORM 实现的 Bot QPM 配置仓库。"""

from sqlalchemy import func

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._orm_model import BotQpmConfigModel
from ._protocol import BotQpmRepository
from ._record import BotQpmRecord

log = get_logger("orm-repository")


class OrmBotQpmRepository(BotQpmRepository, OrmConnectionMixin):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def list_all(self) -> list[BotQpmRecord]:
        env = get_current_env()
        rows = (
            self._session.query(BotQpmConfigModel)
            .filter(BotQpmConfigModel.env == env)
            .all()
        )
        return [r.to_record() for r in rows]

    @with_orm_session
    def get_by_bot_id(self, bot_id: str) -> BotQpmRecord | None:
        env = get_current_env()
        row = (
            self._session.query(BotQpmConfigModel)
            .filter(
                BotQpmConfigModel.bot_id == bot_id,
                BotQpmConfigModel.env == env,
            )
            .first()
        )
        return row.to_record() if row else None

    @with_orm_session
    def upsert(self, *, bot_id: str, qpm: int) -> None:
        env = get_current_env()
        updated = (
            self._session.query(BotQpmConfigModel)
            .filter(
                BotQpmConfigModel.bot_id == bot_id,
                BotQpmConfigModel.env == env,
            )
            .update(
                {"qpm": qpm, "gmt_modified": func.now()},
                synchronize_session=False,
            )
        )
        if updated == 0:
            self._session.add(BotQpmConfigModel(bot_id=bot_id, qpm=qpm, env=env))
            self._session.flush()
        log.info("[bot-qpm:upsert] bot_id=%s qpm=%s env=%s", bot_id, qpm, env)

    @with_orm_session
    def delete(self, bot_id: str) -> bool:
        env = get_current_env()
        deleted = (
            self._session.query(BotQpmConfigModel)
            .filter(
                BotQpmConfigModel.bot_id == bot_id,
                BotQpmConfigModel.env == env,
            )
            .delete(synchronize_session=False)
        )
        if deleted:
            log.info("[bot-qpm:delete] bot_id=%s env=%s", bot_id, env)
        return deleted > 0
