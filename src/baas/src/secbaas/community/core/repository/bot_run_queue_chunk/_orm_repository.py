from sqlalchemy import func

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import BotRunQueueChunkModel
from ._record import BotRunQueueChunkRecord

log = get_logger("orm-repository")


class OrmBotRunQueueChunkRepository(OrmConnectionMixin):
    """baas_bot_run_queue_chunk ORM 仓库实现。"""

    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_chunk(
        self,
        *,
        run_id: str,
        seq: int,
        chunk_type: str,
        content: str | None = None,
        metadata: str | None = None,
    ) -> None:
        row = BotRunQueueChunkModel(
            run_id=run_id,
            seq=seq,
            chunk_type=chunk_type,
            content=content,
            metadata_=metadata,
        )
        self._session.add(row)
        self._session.flush()

    @with_orm_session
    def get_chunks_after(
        self, run_id: str, last_seq: int, *, limit: int = 50
    ) -> list[BotRunQueueChunkRecord]:
        rows = (
            self._session.query(BotRunQueueChunkModel)
            .filter(
                BotRunQueueChunkModel.run_id == run_id,
                BotRunQueueChunkModel.seq > last_seq,
            )
            .order_by(BotRunQueueChunkModel.seq.asc())
            .limit(limit)
            .all()
        )
        return [r.to_record() for r in rows]

    @with_orm_session
    def get_max_seq(self, run_id: str) -> int | None:
        result = (
            self._session.query(func.max(BotRunQueueChunkModel.seq))
            .filter(BotRunQueueChunkModel.run_id == run_id)
            .scalar()
        )
        return int(result) if result is not None else None

    @with_orm_session
    def delete_chunks_by_run(self, run_id: str) -> None:
        self._session.query(BotRunQueueChunkModel).filter(
            BotRunQueueChunkModel.run_id == run_id
        ).delete(synchronize_session=False)
