from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func

from secbaas.community.spi.database import Base

from ._record import PublishRecord


class PublishModel(Base):
    __tablename__ = "baas_publish"

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
    bot_id = Column(Integer, nullable=False)
    publish_type = Column(String(32), nullable=False)
    name = Column(String(256), nullable=True)
    description = Column(String(512), nullable=True)
    publisher = Column(String(128), nullable=True)
    replica_desired = Column(Integer, nullable=True)
    batch_capacity = Column(Integer, nullable=True)
    batch_number = Column(Integer, nullable=True)
    cooldown_seconds = Column(Integer, nullable=True)
    config_version = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False)
    last_publish_id = Column(Integer, nullable=True)
    changelog = Column(Text, nullable=True)
    extra_config = Column(Text, nullable=True)

    def to_record(self) -> PublishRecord:
        import json

        try:
            ec = (
                json.loads(self.extra_config)
                if isinstance(self.extra_config, str)
                else self.extra_config
            ) or {}
        except (json.JSONDecodeError, TypeError):
            ec = {}
        return PublishRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            tenant=self.tenant,
            env=self.env,
            domain=self.domain,
            is_deleted=self.is_deleted or 0,
            creator=self.creator,
            modifier=self.modifier,
            bot_id=self.bot_id,
            publish_type=self.publish_type,
            name=self.name,
            description=self.description,
            publisher=self.publisher,
            replica_desired=self.replica_desired,
            batch_capacity=self.batch_capacity,
            batch_number=self.batch_number,
            cooldown_seconds=self.cooldown_seconds,
            config_version=self.config_version,
            status=self.status,
            last_publish_id=self.last_publish_id,
            changelog=self.changelog,
            extra_config=ec,
        )
