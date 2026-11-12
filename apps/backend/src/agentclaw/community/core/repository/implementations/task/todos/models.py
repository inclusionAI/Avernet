"""Tenant-scoped ORM models for Todo product rows and acceptance decisions."""

from __future__ import annotations

import json

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
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
    TodoExecutionRecord,
    TodoExecutionStatus,
)
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


class TodoExecutionModel(Base):
    """One active or historical Bot execution credential for a Todo."""

    __tablename__ = "ac_todo_execution"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    execution_id = Column(String(64), nullable=False)
    todo_id = Column(String(64), nullable=False)
    collab_context_id = Column(String(256), nullable=False)
    run_id = Column(String(256), nullable=True)
    token_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    executor_id = Column(String(256), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("avernet_tenant", "env", "execution_id", name="uk_todo_execution_tenant_env_id"),
        Index("idx_todo_execution_active", "avernet_tenant", "env", "todo_id", "status", "gmt_create", "id"),
    )

    def to_record(self) -> TodoExecutionRecord:
        return TodoExecutionRecord(
            execution_id=self.execution_id,
            todo_id=self.todo_id,
            collab_context_id=self.collab_context_id,
            run_id=self.run_id,
            token_hash=self.token_hash,
            status=TodoExecutionStatus(self.status),
            executor_id=self.executor_id,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


class TodoArtifactModel(Base):
    """One persisted Todo artifact; file rows contain only a stable object key."""

    __tablename__ = "ac_todo_artifact"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    artifact_id = Column(String(64), nullable=False)
    todo_id = Column(String(64), nullable=False)
    collab_context_id = Column(String(256), nullable=False)
    execution_id = Column(String(64), nullable=False)
    artifact_name = Column(String(256), nullable=False)
    artifact_kind = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    creator = Column(String(256), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("avernet_tenant", "env", "artifact_id", name="uk_todo_artifact_tenant_env_id"),
        UniqueConstraint("avernet_tenant", "env", "todo_id", "execution_id", "content_hash", name="uk_todo_artifact_content"),
        Index("idx_todo_artifact_list", "avernet_tenant", "env", "todo_id", "gmt_create", "id"),
    )

    def to_record(self) -> TodoArtifactRecord:
        return TodoArtifactRecord(
            artifact_id=self.artifact_id,
            todo_id=self.todo_id,
            collab_context_id=self.collab_context_id,
            execution_id=self.execution_id,
            artifact_name=self.artifact_name,
            artifact_kind=TodoArtifactKind(self.artifact_kind),
            content=json.loads(self.content),
            content_hash=self.content_hash,
            creator=self.creator,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


for _model in (TodoModel, TodoAcceptanceModel, TodoExecutionModel, TodoArtifactModel):
    register_avernet_tenant_guard(_model)
