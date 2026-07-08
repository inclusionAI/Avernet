import json

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    func,
)

from secbaas.spi.database import Base

from ._record import BotRunRecord


class BotRunModel(Base):
    __tablename__ = "baas_bot_run"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    run_id = Column(String(128), nullable=False)
    bot_id = Column(String(128), nullable=False)
    api_key_prefix = Column(String(8), nullable=False)
    message = Column(String(256), nullable=True)
    message_long = Column(Text, nullable=True)
    metadata_ = Column("metadata", Text, nullable=True)
    status = Column(String(32), nullable=False)
    result_content = Column(String(256), nullable=True)
    result_content_long = Column(Text, nullable=True)
    result_extra = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("run_id", name="uq_baas_bot_run_run_id"),)

    def to_record(self) -> BotRunRecord:
        return BotRunRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            run_id=self.run_id,
            bot_id=self.bot_id,
            api_key_prefix=self.api_key_prefix,
            message=self.message_long or self.message or "",
            message_long=self.message_long,
            metadata=self._parse_metadata(),
            status=self.status,
            result_content=self.result_content_long or self.result_content or "",
            result_content_long=self.result_content_long,
            result_extra=self._parse_result_extra(),
            error=self.error,
            completed_at=self.completed_at,
        )

    def _parse_metadata(self) -> dict | None:
        try:
            if isinstance(self.metadata_, str):
                return json.loads(self.metadata_)
        except (json.JSONDecodeError, TypeError):
            return None
        return self.metadata_

    def _parse_result_extra(self) -> dict | None:
        try:
            if isinstance(self.result_extra, str):
                return json.loads(self.result_extra)
        except (json.JSONDecodeError, TypeError):
            return None
        return self.result_extra
