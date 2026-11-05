import re
import sqlite3

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import BotRunQueueChunkModel
from ._record import BotRunQueueChunkRecord

log = get_logger("orm")


def _is_run_seq_duplicate(error: IntegrityError) -> bool:
    """Fail closed: only the chunk table's run/sequence unique key is ignorable."""
    original = error.orig
    sqlite_code = getattr(original, "sqlite_errorcode", None)
    if sqlite_code is not None:
        return (
            sqlite_code == sqlite3.SQLITE_CONSTRAINT_UNIQUE
            and str(original)
            == "UNIQUE constraint failed: baas_bot_run_queue_chunk.run_id, "
            "baas_bot_run_queue_chunk.seq"
        )
    code = getattr(original, "errno", None)
    if code is None and original.args:
        code = original.args[0]
    message = getattr(original, "msg", None)
    if message is None and len(original.args) > 1:
        message = original.args[1]
    if not isinstance(message, str):
        return False
    return (
        isinstance(code, int)
        and code == 1062
        and re.search(
            r"for key ['`](?:baas_bot_run_queue_chunk\.)?(?:uk_run_seq|uq_run_seq)['`]$",
            message,
        )
        is not None
    )


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
        try:
            self._session.add(row)
            self._session.flush()
        except IntegrityError as error:
            if not _is_run_seq_duplicate(error):
                raise
            # with_orm_session owns a fresh session for this single insert.
            # Clear the failed transaction without updating the first writer.
            self._session.rollback()

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
