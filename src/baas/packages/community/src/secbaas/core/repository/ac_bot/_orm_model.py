from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func

from secbaas.spi.database import Base


class AcBotModel(Base):
    __tablename__ = "ac_bots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bot_id = Column(String(128), nullable=False)
    bot_name = Column(String(256), nullable=True)
    bot_desc = Column(Text, nullable=True)
    entity_id = Column(String(128), nullable=False)
    entity_type = Column(String(64), nullable=True)
    creator_id = Column(String(64), nullable=True)
    owner_id = Column(String(64), nullable=True)
    engine_types = Column(Text, nullable=True)
    bot_type = Column(String(32), nullable=False)
    active_engine = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False)
    binding_id = Column(Integer, nullable=True)
    modifier_id = Column(String(64), nullable=True)
    share_policy = Column(Text, nullable=True)
    device_id = Column(String(128), nullable=True)
    env = Column(String(32), nullable=False)
    owner_name = Column(String(128), nullable=True)
    public = Column(String(16), nullable=True)
    ext = Column(Text, nullable=True)
    template_type = Column(String(64), nullable=True)
    is_delete = Column(Integer, nullable=False, default=0)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
