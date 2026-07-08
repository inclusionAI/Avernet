from sqlalchemy import BigInteger, Column, DateTime, String, Text, func

from secbaas.spi.database import Base

from ._record import DeviceBindingRecord


class DeviceBindingModel(Base):
    __tablename__ = "ac_entity_device_binding"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    entity_id = Column(String(128), nullable=False)
    entity_type = Column(String(64), nullable=False)
    device_id = Column(String(128), nullable=False)
    device_provider = Column(String(64), nullable=False)
    env = Column(String(32), nullable=False)
    device_props = Column(Text, nullable=True)
    status = Column(String(32), nullable=False)
    apply_reason = Column(String(512), nullable=True)
    applied_by = Column(String(128), nullable=False)
    release_reason = Column(String(512), nullable=True)
    released_by = Column(String(128), nullable=True)
    released_at = Column(DateTime, nullable=True)
    last_alive_at = Column(DateTime, nullable=True)

    def to_record(self) -> DeviceBindingRecord:
        import json

        try:
            dps = (
                json.loads(self.device_props)
                if isinstance(self.device_props, str)
                else self.device_props
            )
        except (json.JSONDecodeError, TypeError):
            dps = {}
        return DeviceBindingRecord(
            id=self.id,
            entity_id=self.entity_id,
            entity_type=self.entity_type,
            device_id=self.device_id,
            device_provider=self.device_provider,
            env=self.env,
            device_props=dps or {},
            status=self.status,
            apply_reason=self.apply_reason,
            applied_by=self.applied_by,
            release_reason=self.release_reason,
            released_by=self.released_by,
            released_at=self.released_at,
            last_alive_at=self.last_alive_at,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
