"""SQLAlchemy ORM model for baas_device table."""

import json

from sqlalchemy import BigInteger, Column, DateTime, String, Text, func

from secbaas.community.logger import get_logger
from secbaas.community.spi.database import Base

from ._record import DeviceRecord

logger = get_logger("orm-model")


class DeviceModel(Base):
    __tablename__ = "baas_device"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_deleted = Column(BigInteger, nullable=False, default=0)
    device_uuid = Column(String(64), nullable=False)
    tenant = Column(String(128), nullable=False)
    env = Column(String(32), nullable=False)
    domain = Column(String(128), nullable=False, default="")
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    provider_type = Column(String(64), nullable=True)
    provider_device_id = Column(String(128), nullable=True)
    provider_device_props = Column(Text, nullable=True)
    extra_config = Column(Text, nullable=True)
    err_msg = Column(String(512), nullable=True)

    def to_record(self) -> DeviceRecord:
        provider_device_props = (
            json.loads(self.provider_device_props)
            if isinstance(self.provider_device_props, str)
            else self.provider_device_props
        ) or {}
        extra_config = (
            json.loads(self.extra_config)
            if isinstance(self.extra_config, str)
            else self.extra_config
        ) or {}
        if not isinstance(extra_config, dict):
            logger.warning(
                "Device id=%s: extra_config has unexpected type %s, defaulting to {}",
                self.id,
                type(extra_config).__name__,
            )
            extra_config = {}

        return DeviceRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            is_deleted=self.is_deleted or 0,
            device_uuid=self.device_uuid,
            tenant=self.tenant,
            env=self.env,
            domain=self.domain,
            creator=self.creator,
            modifier=self.modifier,
            status=self.status,
            provider_type=self.provider_type,
            provider_device_id=self.provider_device_id,
            provider_device_props=provider_device_props,
            extra_config=extra_config,
            err_msg=self.err_msg,
        )
