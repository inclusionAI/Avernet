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
from agentclaw.community.core.task.todos.auto_import_models import (
    TodoAutoImportProgressRecord,
    TodoAutoImportProgressStatus,
    TodoAutoImportRuleRecord,
    TodoAutoImportRuleStatus,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.stage_models import (
    TodoPendingActionKind,
    TodoStageRecord,
    TodoStageStatus,
)
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
    template_id = Column(String(64), nullable=False)
    template_version_id = Column(String(64), nullable=False)
    template_name_snapshot = Column(String(200), nullable=False)
    current_stage_instance_id = Column(String(64), nullable=True)
    pending_action_kind = Column(String(32), nullable=True)
    state_reason = Column(Text, nullable=True)
    requested_action = Column(Text, nullable=True)
    participant_bindings_json = Column(Text, nullable=False, default="[]")
    bot_id = Column(String(256), nullable=True)
    bot_name = Column(String(256), nullable=True)
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
            template_id=self.template_id,
            template_version_id=self.template_version_id,
            template_name_snapshot=self.template_name_snapshot,
            current_stage_instance_id=self.current_stage_instance_id,
            pending_action_kind=self.pending_action_kind,
            state_reason=self.state_reason,
            requested_action=self.requested_action,
            bot_id=self.bot_id or "",
            bot_name=self.bot_name or "",
            status=TodoStatus(self.status),
            collab_context_id=self.collab_context_id,
            created_by=self.created_by,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )



class TodoStageInstanceModel(Base):
    """One concrete Todo stage with all identities fixed at Todo creation."""

    __tablename__ = "ac_todo_stage_instance"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    stage_instance_id = Column(String(64), nullable=False)
    todo_id = Column(String(64), nullable=False)
    template_version_id = Column(String(64), nullable=False)
    stage_key = Column(String(64), nullable=False)
    stage_order = Column(Integer, nullable=False)
    stage_name = Column(String(200), nullable=False)
    purpose = Column(Text, nullable=False)
    stage_kind = Column(String(16), nullable=False)
    executor_binding_key = Column(String(64), nullable=True)
    executor_subject_type = Column(String(16), nullable=False)
    executor_subject_id = Column(String(256), nullable=False)
    executor_subject_name = Column(String(256), nullable=False)
    owner_binding_key = Column(String(64), nullable=True)
    owner_user_id = Column(String(256), nullable=False)
    owner_user_name = Column(String(256), nullable=False)
    completion_gate_json = Column(Text, nullable=False)
    status = Column(String(32), nullable=False)
    collab_context_id = Column(String(256), nullable=True)
    pending_action_kind = Column(String(32), nullable=True)
    state_reason = Column(Text, nullable=True)
    requested_action = Column(Text, nullable=True)
    last_outcome_key = Column(String(256), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
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
            "stage_instance_id",
            name="uk_todo_stage_instance_tenant_env_id",
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "todo_id",
            "stage_key",
            name="uk_todo_stage_instance_tenant_env_todo_stage",
        ),
        Index(
            "idx_todo_stage_list",
            "avernet_tenant",
            "env",
            "todo_id",
            "stage_order",
            "id",
        ),
    )

    def to_record(self) -> TodoStageRecord:
        return TodoStageRecord(
            stage_instance_id=self.stage_instance_id,
            todo_id=self.todo_id,
            template_version_id=self.template_version_id,
            stage_key=self.stage_key,
            stage_order=self.stage_order,
            stage_name=self.stage_name,
            purpose=self.purpose,
            stage_kind=self.stage_kind,
            executor_binding_key=self.executor_binding_key,
            executor_subject_type=self.executor_subject_type,
            executor_subject_id=self.executor_subject_id,
            executor_subject_name=self.executor_subject_name,
            owner_binding_key=self.owner_binding_key,
            owner_user_id=self.owner_user_id,
            owner_user_name=self.owner_user_name,
            completion_gate_json=self.completion_gate_json,
            status=TodoStageStatus(self.status),
            collab_context_id=self.collab_context_id,
            pending_action_kind=TodoPendingActionKind(self.pending_action_kind)
            if self.pending_action_kind
            else None,
            state_reason=self.state_reason,
            requested_action=self.requested_action,
            started_at=self.started_at,
            completed_at=self.completed_at,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
            last_outcome_key=self.last_outcome_key,
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


class TodoSourceBindingModel(Base):
    """Stable imported-source identity for one Todo in one product Space."""

    __tablename__ = "ac_todo_source_binding"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    todo_id = Column(String(64), nullable=False)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    source_type = Column(String(32), nullable=False)
    source_container_id = Column(String(128), nullable=False)
    source_item_id = Column(String(128), nullable=False)
    source_url = Column(String(2048), nullable=True)
    source_updated_at = Column(String(128), nullable=True)
    source_metadata_json = Column(Text, nullable=False, default="{}")
    imported_by = Column(String(256), nullable=False)
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
            name="uk_todo_source_binding_todo",
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "space_id",
            "source_type",
            "source_item_id",
            name="uk_todo_source_binding_identity",
        ),
        Index(
            "idx_todo_source_binding_lookup",
            "avernet_tenant",
            "env",
            "source_type",
            "source_item_id",
        ),
    )


class TodoAutoImportRuleModel(Base):
    """One user-owned configuration consumed by an external import Agent."""

    __tablename__ = "ac_todo_source_import_rule"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    rule_id = Column(String(64), nullable=False)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    source_type = Column(String(32), nullable=False, default="dima")
    source_container_ids_json = Column(Text, nullable=False)
    filter_text = Column(Text, nullable=False)
    bot_id = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False)
    cursor_updated_at = Column(DateTime, nullable=True)
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
            "rule_id",
            name="uk_todo_source_import_rule_id",
        ),
        Index(
            "idx_todo_source_import_rule_owner",
            "avernet_tenant",
            "env",
            "created_by",
            "status",
            "gmt_modified",
            "id",
        ),
        Index(
            "idx_todo_source_import_rule_space",
            "avernet_tenant",
            "env",
            "space_id",
            "status",
            "gmt_modified",
            "id",
        ),
    )

    def to_record(self) -> TodoAutoImportRuleRecord:
        container_ids = json.loads(self.source_container_ids_json)
        return TodoAutoImportRuleRecord(
            rule_id=self.rule_id,
            space_id=self.space_id,
            source_container_ids=tuple(str(item) for item in container_ids),
            filter_text=self.filter_text,
            bot_id=self.bot_id,
            status=TodoAutoImportRuleStatus(self.status),
            cursor_updated_at=self.cursor_updated_at,
            created_by=self.created_by,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


class TodoAutoImportProgressModel(Base):
    """One retry-safe outcome for a version of a Dima work item."""

    __tablename__ = "ac_todo_source_import_progress"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    progress_id = Column(String(64), nullable=False)
    rule_id = Column(String(64), nullable=False)
    source_container_id = Column(String(128), nullable=False)
    source_item_id = Column(String(128), nullable=False)
    source_updated_at = Column(DateTime, nullable=False)
    status = Column(String(32), nullable=False)
    todo_id = Column(String(64), nullable=True)
    detail = Column(Text, nullable=False, default="")
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
            "progress_id",
            name="uk_todo_source_import_progress_id",
        ),
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "rule_id",
            "source_container_id",
            "source_item_id",
            "source_updated_at",
            name="uk_todo_source_import_progress_version",
        ),
        Index(
            "idx_todo_source_import_progress_rule",
            "avernet_tenant",
            "env",
            "rule_id",
            "source_updated_at",
            "id",
        ),
    )

    def to_record(self) -> TodoAutoImportProgressRecord:
        return TodoAutoImportProgressRecord(
            progress_id=self.progress_id,
            rule_id=self.rule_id,
            source_container_id=self.source_container_id,
            source_item_id=self.source_item_id,
            source_updated_at=self.source_updated_at,
            status=TodoAutoImportProgressStatus(self.status),
            todo_id=self.todo_id,
            detail=self.detail,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
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
    stage_instance_id = Column(String(64), nullable=True)
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
            stage_instance_id=self.stage_instance_id,
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


for _model in (
    TodoModel,
    TodoStageInstanceModel,
    TodoAcceptanceModel,
    TodoSourceBindingModel,
    TodoAutoImportRuleModel,
    TodoAutoImportProgressModel,
    TodoExecutionModel,
    TodoArtifactModel,
):
    register_avernet_tenant_guard(_model)
