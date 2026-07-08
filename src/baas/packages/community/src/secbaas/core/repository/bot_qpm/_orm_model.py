from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)

from secbaas.spi.database import Base

from ._record import BotQpmRecord


class BotQpmConfigModel(Base):
    __tablename__ = "baas_bot_qpm_config"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    bot_id = Column(String(128), nullable=False)
    qpm = Column(Integer, nullable=False, server_default=text("60"), default=60)
    env = Column(String(32), nullable=True)

    __table_args__ = (UniqueConstraint("bot_id", "env", name="uk_bot_qpm_bot_env"),)

    def to_record(self) -> BotQpmRecord:
        return BotQpmRecord(
            id=self.id,
            bot_id=self.bot_id,
            qpm=self.qpm,
            env=self.env,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
