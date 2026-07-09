"""SQLAlchemy ORM models for system configuration tables (local/SQLite mode).

两表设计：
- AcConfigCategory: 分类目录表 (ac_config_category)
- AcConfigItem: 配置项表 (ac_config_item)

配置项通过 parent_id 关联分类目录。
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Index, UniqueConstraint, ForeignKey, func
from agentclaw.community.core.base import Base


class AcConfigCategory(Base):
    """配置分类目录表"""
    __tablename__ = "ac_config_category"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(64), nullable=False)
    category_name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    env = Column(String(32), nullable=False, default="prod")
    creator = Column(String(128), nullable=True)
    modifier = Column(String(128), nullable=True)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # 约束
    __table_args__ = (
        UniqueConstraint("category", "env", name="uk_category_env"),
        Index("idx_env", "env"),
    )


class AcConfigItem(Base):
    """配置项表"""
    __tablename__ = "ac_config_item"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, ForeignKey("ac_config_category.id", ondelete="CASCADE"), nullable=False)
    config_key = Column(String(128), nullable=False)
    config_value = Column(Text, nullable=False)
    description = Column(String(512), nullable=True)
    creator = Column(String(128), nullable=True)
    modifier = Column(String(128), nullable=True)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # 约束
    __table_args__ = (
        UniqueConstraint("parent_id", "config_key", name="uk_parent_key"),
        Index("idx_parent_id", "parent_id"),
    )
