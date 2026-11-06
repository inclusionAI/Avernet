from sqlalchemy import func, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DatabaseError

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session

from ._orm_model import BotRunQueueChunkModel
from ._record import BotRunQueueChunkRecord


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
        table = BotRunQueueChunkModel.__table__
        values = dict(
            run_id=run_id,
            seq=seq,
            chunk_type=chunk_type,
            content=content,
            metadata=metadata,
        )
        dialect = self._session.bind.dialect.name
        if dialect == "sqlite":
            statement = (
                sqlite_insert(table)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["run_id", "seq"])
            )
        elif dialect in ("mysql", "mariadb"):
            statement = mysql_insert(table).values(**values).prefix_with("IGNORE")
        else:
            raise ValueError(f"Unsupported database dialect: {dialect}")
        self._session.execute(statement)
        if dialect in ("mysql", "mariadb"):
            # IGNORE also downgrades invalid data to warnings. Diagnostic SHOW
            # statements preserve the list; SELECT @@warning_count would clear it.
            count = self._session.execute(text("SHOW COUNT(*) WARNINGS")).scalar_one()
            if type(count) is not int or count < 0:
                raise DatabaseError(
                    None, None, RuntimeError("Invalid chunk insert warnings count")
                )
            if count:
                warnings = self._session.execute(text("SHOW WARNINGS")).all()
                # max_error_count can suppress/truncate the stored diagnostics.
                # Fail closed rather than commit coerced data as a valid chunk.
                rejected = [(row[1], row[2]) for row in warnings if row[1] != 1062]
                if len(warnings) != count or rejected:
                    raise DatabaseError(
                        None,
                        None,
                        RuntimeError(
                            "Nonduplicate or incomplete chunk insert warnings: "
                            f"reported={count}, stored={len(warnings)}, "
                            f"rejected={rejected!r}"
                        ),
                    )

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
