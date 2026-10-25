"""One immutable snapshot per space revision, with its delivery acknowledgement."""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from agentclaw.community.core.base import Base


class SpaceKnowledgeSyncModel(Base):
    __tablename__ = "ac_space_knowledge_sync"

    env = Column(String(20), primary_key=True)
    space_id = Column(BigInteger, primary_key=True)
    revision = Column(BigInteger, primary_key=True, autoincrement=False)
    app = Column(String(32), nullable=False)
    # Full rosters can exceed MySQL TEXT's 64 KiB. Only this new table needs
    # MEDIUMTEXT; shared queue tasks contain small space/revision pointers.
    payload = Column(Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    acknowledged = Column(Boolean, nullable=False, default=False, server_default="0")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "idx_space_sync_pending",
            "env",
            "app",
            "acknowledged",
            "space_id",
            "revision",
        ),
        {"mysql_engine": "InnoDB"},
    )
