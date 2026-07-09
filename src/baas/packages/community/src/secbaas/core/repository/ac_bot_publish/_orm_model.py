from sqlalchemy import BigInteger, Column, DateTime, String, Text, func

from secbaas.spi.database import Base


class AcBotPublishModel(Base):
    __tablename__ = "ac_bot_publish"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_bot_pk = Column(BigInteger, nullable=False)
    source_bot_id = Column(String(512), nullable=False)
    publish_bot_id = Column(String(1024), nullable=False)
    name = Column(String(2048), nullable=False)
    description = Column(String(4096), nullable=True)
    owner_id = Column(String(128), nullable=False)
    owner_name = Column(String(128), nullable=True)
    status = Column(String(128), nullable=False)
    version = Column(BigInteger, nullable=True)
    last_pub_id = Column(BigInteger, nullable=False)
    env = Column(String(64), nullable=False)
    ext = Column(Text, nullable=True)
    permission_owner = Column(String(64), nullable=False)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
