"""SQLAlchemy model for ``ac_session_resource``."""
from __future__ import annotations

import json

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
    TransferApiVersion,
)

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class SessionResourceModel(Base):
    __tablename__ = "ac_session_resource"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    resource_id = Column(String(128), nullable=False, unique=True)
    owner_id = Column(String(128), nullable=False)
    bot_id = Column(String(128), nullable=False)
    binding_id = Column(BigInteger, nullable=True)
    scope_type = Column(String(64), nullable=False)
    scope_key_hash = Column(String(128), nullable=False)
    session_key_hash = Column(String(128), nullable=False)
    engine_type = Column(String(64), nullable=False)
    tenant = Column(String(128), nullable=False)
    bot_uuid = Column(String(128), nullable=False)
    display_name = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    device_path = Column(String(2048), nullable=False)
    workspace_relative_path = Column(String(2048), nullable=False)
    transfer_id = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False)
    transfer_api_version = Column(
        String(32),
        nullable=False,
        default=TransferApiVersion.BOT_DEVICE_V1.value,
        server_default=TransferApiVersion.BOT_DEVICE_V1.value,
    )
    session_key_ciphertext = Column(Text, nullable=True)
    task_id = Column(String(128), nullable=True)
    task_version = Column(Integer, nullable=False, default=0)
    size_bytes = Column(BigInteger, nullable=True)
    client_content_hash = Column(String(128), nullable=True)
    materialized_ref_json = Column(Text, nullable=True)
    error_code = Column(String(128), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index(
            "idx_session_resource_owner_bot_session",
            "owner_id",
            "bot_id",
            "session_key_hash",
        ),
        Index("idx_session_resource_task", "task_id", "task_version"),
    )

    def to_record(self) -> SessionResourceRecord:
        return SessionResourceRecord(
            resource_id=self.resource_id,
            owner_id=self.owner_id,
            bot_id=self.bot_id,
            binding_id=self.binding_id,
            scope_type=self.scope_type,
            scope_key_hash=self.scope_key_hash,
            session_key_hash=self.session_key_hash,
            engine_type=self.engine_type,
            tenant=self.tenant,
            bot_uuid=self.bot_uuid,
            display_name=self.display_name,
            filename=self.filename,
            device_path=self.device_path,
            workspace_relative_path=self.workspace_relative_path,
            transfer_id=self.transfer_id,
            status=SessionResourceStatus(self.status),
            transfer_api_version=TransferApiVersion(self.transfer_api_version),
            session_key_ciphertext=self.session_key_ciphertext,
            task_id=self.task_id,
            task_version=self.task_version,
            size_bytes=self.size_bytes,
            client_content_hash=self.client_content_hash,
            materialized_ref=(
                json.loads(self.materialized_ref_json)
                if self.materialized_ref_json
                else None
            ),
            error_code=self.error_code,
            deleted_at=self.deleted_at,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
