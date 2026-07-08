from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func

from secbaas.spi.database import Base

from ._record import APIKeyRecord


class APIKeyModel(Base):
    __tablename__ = "baas_api_key"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    api_key_hash = Column(String(256), nullable=False)
    api_key_prefix = Column(String(8), nullable=False)
    key_name = Column(String(128), nullable=True)
    app_id = Column(String(128), nullable=False)
    app_type = Column(String(64), nullable=True)
    description = Column(String(512), nullable=True)
    rate_limit_rpm = Column(Integer, nullable=True)
    rate_limit_rpd = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False)
    owner = Column(String(128), nullable=False)
    tenant = Column(String(128), nullable=True)
    env = Column(String(32), nullable=False)
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=True)
    policy = Column(Text, nullable=True)

    def to_record(self) -> APIKeyRecord:
        return APIKeyRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            api_key_hash=self.api_key_hash,
            api_key_prefix=self.api_key_prefix,
            key_name=self.key_name,
            app_id=self.app_id,
            app_type=self.app_type,
            description=self.description,
            rate_limit_rpm=self.rate_limit_rpm,
            rate_limit_rpd=self.rate_limit_rpd,
            status=self.status,
            owner=self.owner,
            tenant=self.tenant,
            env=self.env,
            creator=self.creator,
            modifier=self.modifier,
            policy=self.policy,
        )
