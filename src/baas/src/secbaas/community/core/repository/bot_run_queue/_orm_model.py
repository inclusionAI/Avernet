from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)

from secbaas.community.spi.database import Base

from ._record import BotRunQueueRecord, _parse_meta_json


class BotRunQueueModel(Base):
    """``baas_bot_run_queue`` 队列工作项表（与 ``baas_bot_run`` 1:1，按 run_id 关联）。

    DDL 见 ``sqls/migrate_bot_run_queue.sql``（纯 CREATE TABLE，不触碰既有热表）。
    """

    __tablename__ = "baas_bot_run_queue"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    run_id = Column(String(128), nullable=False)
    bot_id = Column(String(128), nullable=False)
    session_id = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False)
    assigned_worker = Column(String(64), nullable=True)
    last_heartbeat = Column(DateTime, nullable=True)
    meta = Column(Text, nullable=True)
    env = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", name="uk_bot_run_queue_run_id"),
        # 发现/认领：WHERE status='PENDING' ... bot_id ... ORDER BY gmt_create
        Index("idx_q_status_env_bot_created", "status", "env", "bot_id", "gmt_create"),
        # 恢复：WHERE status='RUNNING' AND last_heartbeat < ?
        Index("idx_q_status_heartbeat", "status", "last_heartbeat"),
    )

    def to_record(self) -> BotRunQueueRecord:
        return BotRunQueueRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            run_id=self.run_id,
            bot_id=self.bot_id,
            session_id=self.session_id,
            status=self.status,
            assigned_worker=self.assigned_worker,
            last_heartbeat=self.last_heartbeat,
            meta=_parse_meta_json(self.meta),
            env=self.env,
        )
