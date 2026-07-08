"""
SQLAlchemy ORM models for Policy service.

These models are the SQLite equivalents of the MySQL schemas:
    - ac_access_control_policy
    - ac_user_info

They are used in local development mode (DATABASE_MODE=sqlite) to store
policy records in a local SQLite file instead of the remote the corp store database.

The models register with the shared `Base` from plugins/models.py,
so `init_db()` will automatically create these tables when running in SQLite mode.

Key MySQL -> SQLite translation notes:
    - BIGINT AUTO_INCREMENT -> Integer with autoincrement=True
      (SQLite uses ROWID-based autoincrement for INTEGER PRIMARY KEY)
    - MEDIUMTEXT -> Text
      (SQLite has no MEDIUMTEXT type; Text is sufficient)
    - DEFAULT CURRENT_TIMESTAMP -> default=func.now()
      (resolved DB-side at insert time; avoids 8h skew vs prod CST clock)
    - ON UPDATE CURRENT_TIMESTAMP -> onupdate=func.now()
      (SQLite has no trigger-based ON UPDATE; SQLAlchemy's onupdate
       handles this when updates go through the ORM)
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint, func

# 使用 plugins.models.Base 以与 db.py init_db() 中的 Base 保持一致
from agentclaw.community.plugin_api.models import Base


class AccessControlPolicy(Base):
    """Access control policy record.

    Maps an entity (user/team) to its access control policy.
    Each entity_type + entity_id combination is unique.
    """
    __tablename__ = "ac_access_control_policy"

    # Primary key — auto-incrementing integer
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Entity identification
    entity_id = Column(String(1024), nullable=False)
    entity_type = Column(String(1024), nullable=False)

    # Policy content — stored as JSON string or plain text
    policy = Column(Text, nullable=True)

    # Timestamps — auto-managed by SQLAlchemy
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Unique constraint on entity_type + entity_id
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uk_entity_type_id"),
    )


class UserInfo(Base):
    """User information record.

    Tracks user access status and type.
    Each user_id + user_type combination is unique.
    """
    __tablename__ = "ac_user_info"

    # Primary key — auto-incrementing integer
    id = Column(Integer, primary_key=True, autoincrement=True)

    # User identification
    user_id = Column(String(256), nullable=False)
    user_type = Column(String(64), nullable=False)  # NORMAL, COMPETE
    status = Column(String(64), nullable=False)  # ACCESS, REFUSE

    # Timestamps — auto-managed by SQLAlchemy
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Unique constraint on user_id + user_type
    __table_args__ = (
        UniqueConstraint("user_id", "user_type", name="uk_user_id_type"),
    )
