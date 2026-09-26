from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    func,
)

from secbaas.community.spi.database import Base

from ._record import BotSessionRecord


class BotSessionModel(Base):
    __tablename__ = "baas_bot_session"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    bot_uuid = Column(String(64), nullable=False)
    invoker = Column(String(128), nullable=False)
    session_id = Column(String(128), nullable=False)
    req = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
    err_msg = Column(Text, nullable=True)
    context = Column(Text, nullable=True)
    status = Column(String(32), nullable=False)
    device_uuid = Column(String(64), nullable=True)
    env = Column(String(32), nullable=False)
    tenant = Column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", name="uq_baas_bot_session_session_id"),
    )

    def to_record(self) -> BotSessionRecord:
        import json

        def _json(v):
            return json.loads(v) if isinstance(v, str) else v

        try:
            req = _json(self.req) if self.req else None
        except (json.JSONDecodeError, TypeError):
            req = None
        try:
            result = _json(self.result) if self.result else None
        except (json.JSONDecodeError, TypeError):
            result = None
        try:
            ctx = _json(self.context) if self.context else None
        except (json.JSONDecodeError, TypeError):
            ctx = None
        return BotSessionRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            bot_uuid=self.bot_uuid,
            invoker=self.invoker,
            session_id=self.session_id,
            req=req,
            result=result,
            err_msg=self.err_msg,
            context=ctx,
            status=self.status,
            device_uuid=self.device_uuid,
            env=self.env,
            tenant=self.tenant,
        )
