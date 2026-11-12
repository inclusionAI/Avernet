"""Tenant-scoped ORM models for Todo product rows and acceptance decisions."""

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
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class TodoModel(Base):
    """One A-owned product Todo and its B collaboration-context association."""

    __tablename__ = "ac_todo"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    todo_id = Column(String(64), nullable=False)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    bot_id = Column(String(256), nullable=False)
    bot_name = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False)
    collab_context_id = Column(String(256), nullable=True)
    created_by = Column(String(256), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "todo_id",
            name="uk_todo_tenant_env_id",
        ),
        Index(
            "idx_todo_space_status",
            "avernet_tenant",
            "env",
            "space_id",
            "status",
            "gmt_create",
            "id",
        ),
        Index(
            "idx_todo_space_list",
            "avernet_tenant",
            "env",
            "space_id",
            "gmt_create",
            "id",
        ),
        Index(
            "idx_todo_status_scan",
            "avernet_tenant",
            "env",
            "status",
            "gmt_create",
            "id",
        ),
    )

    def to_record(self) -> TodoRecord:
        return TodoRecord(
            todo_id=self.todo_id,
            space_id=self.space_id,
            title=self.title,
            description=self.description,
            bot_id=self.bot_id,
            bot_name=self.bot_name,
            status=TodoStatus(self.status),
            collab_context_id=self.collab_context_id,
            created_by=self.created_by,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


class TodoAcceptanceModel(Base):
    """Append-only Human acceptance decision for a Todo execution round."""

    __tablename__ = "ac_todo_acceptance"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    acceptance_id = Column(String(64), nullable=False)
    todo_id = Column(String(64), nullable=False)
    verdict = Column(String(32), nullable=False)
    message = Column(Text, nullable=False)
    author_id = Column(String(256), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "acceptance_id",
            name="uk_todo_acceptance_tenant_env_id",
        ),
        Index(
            "idx_todo_acceptance_history",
            "avernet_tenant",
            "env",
            "todo_id",
            "gmt_create",
            "id",
        ),
    )


for _model in (TodoModel, TodoAcceptanceModel):
    register_avernet_tenant_guard(_model)
