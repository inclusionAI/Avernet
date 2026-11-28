"""Tenant-scoped ORM models for Todo template identities and versions."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.todos.template_codec import (
    TodoTemplateDefinitionCodec,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateRecord,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
    TodoTemplateVersionStatus,
)
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")
_CODEC = TodoTemplateDefinitionCodec()


class TodoTemplateModel(Base):
    """Stable Todo template metadata and current published-version pointer."""

    __tablename__ = "ac_todo_template"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    template_id = Column(String(64), nullable=False)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(32), nullable=False)
    published_version_id = Column(String(64), nullable=True)
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "template_id",
            name="uk_todo_template_tenant_env_id",
        ),
        Index(
            "idx_todo_template_space_status",
            "avernet_tenant",
            "env",
            "space_id",
            "status",
            "gmt_create",
            "id",
        ),
    )

    def to_record(self) -> TodoTemplateRecord:
        return TodoTemplateRecord(
            template_id=self.template_id,
            space_id=self.space_id,
            name=self.name,
            description=self.description,
            status=TodoTemplateStatus(self.status),
            published_version_id=self.published_version_id,
            created_by=self.created_by,
            updated_by=self.updated_by,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


class TodoTemplateVersionModel(Base):
    """Draft or published Todo template definition version."""

    __tablename__ = "ac_todo_template_version"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    template_version_id = Column(String(64), nullable=False)
    template_id = Column(String(64), nullable=False)
    draft_template_id = Column(String(64), nullable=True)
    version_no = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    schema_version = Column(String(32), nullable=False)
    definition_json = Column(Text, nullable=False)
    definition_hash = Column(String(64), nullable=False)
    created_by = Column(String(256), nullable=False)
    published_by = Column(String(256), nullable=True)
    published_at = Column(DateTime, nullable=True)
    product_profile_id = Column(BigInteger, nullable=True)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "template_version_id",
            name="uk_todo_template_version_tenant_env_id",
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "template_id",
            "version_no",
            name="uk_todo_template_version_no",
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "draft_template_id",
            name="uk_todo_template_single_draft",
        ),
        Index(
            "idx_todo_template_version_history",
            "avernet_tenant",
            "env",
            "template_id",
            "version_no",
            "id",
        ),
        Index(
            "idx_todo_template_version_product_profile",
            "avernet_tenant",
            "env",
            "product_profile_id",
        ),
    )

    def to_record(self) -> TodoTemplateVersionRecord:
        return TodoTemplateVersionRecord(
            template_version_id=self.template_version_id,
            template_id=self.template_id,
            version_no=self.version_no,
            status=TodoTemplateVersionStatus(self.status),
            schema_version=self.schema_version,
            definition=_CODEC.decode(self.definition_json),
            definition_hash=self.definition_hash,
            created_by=self.created_by,
            published_by=self.published_by,
            published_at=self.published_at,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
            product_profile_id=self.product_profile_id,
        )


for _model in (TodoTemplateModel, TodoTemplateVersionModel):
    register_avernet_tenant_guard(_model)
