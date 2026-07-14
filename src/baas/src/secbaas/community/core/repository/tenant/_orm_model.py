"""SQLAlchemy ORM model for baas_tenant table."""

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

from ._record import TenantRecord


class TenantModel(Base):
    __tablename__ = "baas_tenant"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_deleted = Column(BigInteger, nullable=False, default=0)
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    extra_config = Column(Text, nullable=True)
    env = Column(String(32), nullable=False)

    __table_args__ = (UniqueConstraint("name", "env", name="uq_baas_tenant_name_env"),)

    def to_record(self) -> TenantRecord:
        import json

        try:
            ec = (
                json.loads(self.extra_config)
                if isinstance(self.extra_config, str)
                else self.extra_config
            )
        except (json.JSONDecodeError, TypeError):
            ec = {}
        return TenantRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            is_deleted=self.is_deleted or 0,
            creator=self.creator,
            modifier=self.modifier,
            name=self.name,
            description=self.description,
            extra_config=ec or {},
            env=self.env,
        )
