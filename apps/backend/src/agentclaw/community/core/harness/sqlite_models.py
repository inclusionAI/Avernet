"""SQLAlchemy ORM models for Harness Engineering tables (SQLite local mode).

Uses the shared Base from plugins/models.py so init_db() auto-creates them.

Key MySQL/SQLite translation notes:
- BIGINT AUTO_INCREMENT -> Integer with autoincrement=True (SQLite ROWID-based)
- MEDIUMTEXT -> Text (SQLite has no MEDIUMTEXT)
- DEFAULT CURRENT_TIMESTAMP -> default=func.now() (DB-side; avoids UTC vs CST skew)
- ON UPDATE CURRENT_TIMESTAMP -> onupdate=func.now()
"""
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Index,
    UniqueConstraint,
    func,
)

from agentclaw.community.plugin_api.models import Base, AutoIncrementBigInteger, get_current_env


class HarnessPatchModel(Base):
    """ac_harness_patch — patch grouping table."""

    __tablename__ = "ac_harness_patch"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    template_id = Column(Integer, nullable=False)
    record_id = Column(Integer, nullable=False)
    name = Column(String(256), nullable=False)
    layer = Column(String(8), nullable=False)
    description = Column(Text, nullable=True)
    scope = Column(Text, nullable=False, default="all")
    content = Column(Text, nullable=False)
    advise = Column(Text, nullable=True)
    is_applied = Column(String(16), nullable=False, default="N")
    env = Column(String(20), nullable=False, default=get_current_env)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_harness_patch_template_id", "template_id"),
        Index("idx_harness_patch_layer_env", "layer", "env"),
        Index("idx_harness_patch_record_id", "record_id"),
        Index("idx_harness_patch_scope", "scope", "env"),
    )


class HarnessPatchTemplateModel(Base):
    """ac_harness_patch_template — patch template definition table."""

    __tablename__ = "ac_harness_patch_template"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    layer = Column(String(8), nullable=False)
    target = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    description = Column(Text, nullable=True)
    applicable_when = Column(Text, nullable=True)
    operations = Column(Text, nullable=False)
    risk_level = Column(String(16), nullable=False, default="low")
    status = Column(String(32), nullable=False, default="active")
    env = Column(String(20), nullable=False, default=get_current_env)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("name", "env", name="uk_harness_template_name_env"),
        Index("idx_harness_template_layer", "layer", "env"),
        Index("idx_harness_template_status", "status", "env"),
    )


class HarnessScanRecordModel(Base):
    """ac_harness_scan_record — diagnostic scan record table."""

    __tablename__ = "ac_harness_scan_record"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    bot_id = Column(String(1024), nullable=False)
    entity_id = Column(String(128), nullable=False, default="")
    health_score = Column(Integer, nullable=False, default=0)
    score_grade = Column(String(32), nullable=True)
    check_items = Column(Text, nullable=False, default="[]")
    findings = Column(Text, nullable=False, default="[]")
    findings_summary = Column(Text, nullable=False, default="{}")
    trigger_source = Column(String(32), nullable=False, default="api")
    status = Column(String(32), nullable=False, default="completed")
    failed_reason = Column(Text, nullable=True)
    env = Column(String(20), nullable=True)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    scan_dim = Column(String(256), nullable=True)
    patch_ids = Column(Text, nullable=True)
    bot_publish_id = Column(String(128), nullable=True)
    scan_type = Column(String(256), nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    layer = Column(String(256), nullable=False, default="L1")

    __table_args__ = (
        Index("idx_bot_entity_env", "bot_id", "entity_id", "env"),
        Index("idx_bot_entity_create", "bot_id", "entity_id", "gmt_create"),
        Index("idx_status", "status", "env"),
    )


class HarnessPatchRecordModel(Base):
    """ac_harness_patch_record — patch application lifecycle record table."""

    __tablename__ = "ac_harness_patch_record"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    bot_id = Column(String(64), nullable=False)
    entity_id = Column(String(128), nullable=False, default="")
    patch_id = Column(Integer, nullable=False)
    layer = Column(String(8), nullable=False, default="L1")
    target = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="planned")
    operations = Column(Text, nullable=True)
    preview_diff = Column(Text, nullable=True)
    backup_content = Column(Text, nullable=True)
    backup_path = Column(String(512), nullable=True)
    backup_checksum = Column(String(128), nullable=True)
    applied_by = Column(String(128), nullable=False, default="")
    rolled_back_at = Column(DateTime, nullable=True)
    failed_reason = Column(String(1024), nullable=True)
    env = Column(String(20), nullable=False, default=get_current_env)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    bot_publish_id = Column(String(128), nullable=True)

    __table_args__ = (
        Index("idx_harness_app_bot_entity_env", "bot_id", "entity_id", "env"),
        Index("idx_harness_app_patch", "patch_id", "env"),
        Index("idx_harness_app_status", "status", "env"),
        Index("idx_harness_app_bot_entity_status", "bot_id", "entity_id", "status", "env"),
    )
