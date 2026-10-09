"""SQLAlchemy ORM model for baas_bot table."""

import json

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    func,
)

from secbaas.community.spi.database import Base

from ._record import BotRecord


class BotModel(Base):
    __tablename__ = "baas_bot"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_deleted = Column(BigInteger, nullable=False, default=0)
    bot_uuid = Column(String(64), nullable=False)
    tenant = Column(String(128), nullable=False)
    env = Column(String(32), nullable=False)
    domain = Column(String(128), nullable=False, default="")
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    name = Column(String(256), nullable=True)
    description = Column(Text, nullable=True)
    template_uuid = Column(String(64), nullable=True)
    replica_desired = Column(Integer, nullable=True, default=0)
    replica_minimum = Column(Integer, nullable=True, default=0)
    replica_maximum = Column(Integer, nullable=True, default=0)
    auto_scaling_enabled = Column(Boolean, nullable=True, default=False)
    sla_grade = Column(String(32), nullable=True)
    extra_config = Column(Text, nullable=True)

    def to_record(self) -> BotRecord:
        return BotRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            is_deleted=self.is_deleted or 0,
            bot_uuid=self.bot_uuid,
            tenant=self.tenant,
            env=self.env,
            domain=self.domain,
            creator=self.creator,
            modifier=self.modifier,
            status=self.status,
            name=self.name,
            description=self.description,
            template_uuid=self.template_uuid,
            replica_desired=self.replica_desired or 0,
            replica_minimum=self.replica_minimum or 0,
            replica_maximum=self.replica_maximum or 0,
            auto_scaling_enabled=self.auto_scaling_enabled or False,
            sla_grade=self.sla_grade,
            extra_config=self._parse_extra_config(),
        )

    def _parse_extra_config(self) -> dict:
        """Parse extra_config JSON string to dict, with None handling."""
        if self.extra_config is None:
            return {}
        try:
            if isinstance(self.extra_config, str):
                return json.loads(self.extra_config)
        except (json.JSONDecodeError, TypeError):
            pass
        return {}
