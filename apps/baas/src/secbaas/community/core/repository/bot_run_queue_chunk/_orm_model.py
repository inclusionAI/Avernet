from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Column,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from secbaas.community.spi.database import Base

from ._record import BotRunQueueChunkRecord


class BotRunQueueChunkModel(Base):
    """baas_bot_run_queue_chunk 表 ORM model。"""

    __tablename__ = "baas_bot_run_queue_chunk"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(TIMESTAMP, nullable=False, server_default=func.now())
    gmt_modified = Column(
        TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    run_id = Column(String(128), nullable=False)
    seq = Column(Integer, nullable=False)
    chunk_type = Column(String(16), nullable=False)
    content = Column(Text, nullable=True)
    metadata_ = Column("metadata", Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uk_run_seq"),
        Index("idx_run_seq", "run_id", "seq"),
    )

    def to_record(self) -> BotRunQueueChunkRecord:
        return BotRunQueueChunkRecord(
            id=self.id,
            run_id=self.run_id,
            seq=self.seq,
            chunk_type=self.chunk_type,
            content=self.content,
            metadata=self.metadata_,
        )
