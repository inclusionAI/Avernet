"""Delivery progress independent of ac_space; no FK or cascading deletion."""

from sqlalchemy import BigInteger, Column, Index, String

from agentclaw.community.core.base import Base


class SpaceKnowledgeSyncModel(Base):
    __tablename__ = "ac_space_knowledge_sync"

    env = Column(String(20), primary_key=True)
    space_id = Column(BigInteger, primary_key=True)
    app = Column(String(32), nullable=False)
    revision = Column(BigInteger, nullable=False, default=0, server_default="0")
    synced_revision = Column(BigInteger, nullable=False, default=0, server_default="0")
    snapshot_hash = Column(String(64), nullable=False, default="", server_default="")
    status = Column(
        String(16), nullable=False, default="active", server_default="active"
    )

    __table_args__ = (
        Index("idx_space_sync_owner", "env", "app", "space_id"),
        {"mysql_engine": "InnoDB"},
    )
