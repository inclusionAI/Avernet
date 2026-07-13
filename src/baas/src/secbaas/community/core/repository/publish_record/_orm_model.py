from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func

from secbaas.community.spi.database import Base

from ._record import PublishRecordRecord


class PublishRecordModel(Base):
    __tablename__ = "baas_publish_record"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    tenant = Column(String(128), nullable=False)
    env = Column(String(32), nullable=False)
    domain = Column(String(128), nullable=False, default="")
    is_deleted = Column(BigInteger, nullable=False, default=0)
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=False)
    device_id = Column(Integer, nullable=True)
    bot_id = Column(Integer, nullable=True)
    publish_id = Column(Integer, nullable=True)
    batch_id = Column(Integer, nullable=True)
    event_type = Column(String(32), nullable=False)
    trigger_source = Column(String(64), nullable=True)
    publish_reason = Column(String(256), nullable=True)
    result_status = Column(String(32), nullable=False)
    result_message = Column(Text, nullable=True)
    extra_config = Column(Text, nullable=True)

    def to_record(self) -> PublishRecordRecord:
        import json

        try:
            ec = (
                json.loads(self.extra_config)
                if isinstance(self.extra_config, str)
                else self.extra_config
            )
        except (json.JSONDecodeError, TypeError):
            ec = {}
        return PublishRecordRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            tenant=self.tenant,
            env=self.env,
            domain=self.domain,
            is_deleted=self.is_deleted or 0,
            creator=self.creator,
            modifier=self.modifier,
            device_id=self.device_id,
            bot_id=self.bot_id,
            publish_id=self.publish_id,
            batch_id=self.batch_id,
            event_type=self.event_type,
            trigger_source=self.trigger_source,
            publish_reason=self.publish_reason,
            result_status=self.result_status,
            result_message=self.result_message,
            extra_config=ec or {},
        )
