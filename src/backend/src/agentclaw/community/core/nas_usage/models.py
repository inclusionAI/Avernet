"""NAS usage statistics ORM model.

Maps to ac_nas_usage_info table for tracking disk usage of NAS directories.
"""
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, func

from agentclaw.community.plugin_api.models import Base


class NasUsageInfo(Base):
    """NAS usage statistics record.

    Tracks disk usage (MB) and file count for each top-level directory
    under /home/admin/.merge_nas.
    """
    __tablename__ = "ac_nas_usage_info"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    directory_name = Column(String(255), nullable=False, unique=True)
    total_usage_mb = Column(BigInteger, nullable=True)
    file_count = Column(Integer, nullable=True)
    is_delete = Column(Integer, nullable=False, default=0)  # 0=active, 1=deleted
