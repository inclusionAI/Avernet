"""Transport-neutral product inputs for creating Todos with an executor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agentclaw.community.core.errors import ValidationError

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.models import TodoRecord
    from agentclaw.community.core.task.todos.source_models import TodoSourceInput
    from agentclaw.community.core.task.todos.stage_models import TodoStageRecord


class TodoExecutorKind(StrEnum):
    """Product executor variants supported by Todo creation."""

    BOT = "bot"
    BOT_TEAM = "bot_team"


@dataclass(frozen=True)
class TodoExecutorSelection:
    """Selected Bot or immutable Bot-team revision for one Todo."""

    type: TodoExecutorKind
    id: str
    revision_id: str | None = None


@dataclass(frozen=True)
class TodoOwnerInput:
    """Human responsible for the Todo and its planned approval stages."""

    user_id: str
    user_name: str


class TodoRuntimeKind(StrEnum):
    """The single runtime routing decision for one persisted Todo."""

    PRODUCT_COLLET = "product_collet"
    PRODUCT_RUNTIME_PENDING = "product_runtime_pending"
    LEGACY_CONTEXT = "legacy_context"
    INVALID_BINDING = "invalid_binding"


class TodoProductColletStatus(StrEnum):
    """Dynamic product-runtime binding status; not a seventh Todo state."""

    READY = "ready"
    RETRYABLE_FAILURE = "retryable_failure"
    NOT_APPLICABLE = "not_applicable"
    BINDING_CONFLICT = "binding_conflict"
    INVALID_BINDING = "invalid_binding"


@dataclass(frozen=True)
class EnsureProductColletResult:
    """One product Collet compensation result and the resulting Todo view."""

    status: TodoProductColletStatus
    todo: TodoRecord
    error_code: str | None = None


def resolve_todo_runtime_kind(
    todo: TodoRecord,
    *,
    product_profile_id: int | None,
) -> TodoRuntimeKind:
    """Resolve runtime routing from only authoritative persisted bindings."""

    if product_profile_id is None:
        if todo.product_collet_id is not None:
            return TodoRuntimeKind.INVALID_BINDING
        return TodoRuntimeKind.LEGACY_CONTEXT

    if todo.product_collet_id is not None:
        return TodoRuntimeKind.PRODUCT_COLLET
    if todo.collab_context_id is not None:
        return TodoRuntimeKind.INVALID_BINDING
    return TodoRuntimeKind.PRODUCT_RUNTIME_PENDING



def build_product_collet_content(
    *,
    todo: TodoRecord,
    source: TodoSourceInput | None,
    stages: Sequence[TodoStageRecord],
    product_profile_id: int,
) -> str:
    """Build deterministic, bounded product facts for Product Runtime input."""

    if product_profile_id <= 0:
        raise ValidationError("product_profile_id must be positive")
    payload = {
        "version": 1,
        "product_profile_id": product_profile_id,
        "todo": {
            "id": todo.todo_id,
            "space_id": todo.space_id,
            "title": todo.title,
            "description": todo.description,
            "template_id": todo.template_id,
            "template_version_id": todo.template_version_id,
            "template_name": todo.template_name_snapshot,
            "owner_id": todo.owner_id or todo.created_by,
            "owner_name": todo.owner_name or todo.created_by,
        },
        "source": (
            None
            if source is None
            else {
                "type": source.source_type,
                "container_id": source.container_id,
                "item_id": source.item_id,
                "url": source.url,
                "updated_at": source.updated_at,
                "metadata": source.metadata,
            }
        ),
        "stages": [
            {
                "key": stage.stage_key,
                "order": stage.stage_order,
                "name": stage.stage_name,
                "purpose": stage.purpose,
                "kind": stage.stage_kind,
                "executor": {
                    "type": stage.executor_subject_type,
                    "id": stage.executor_subject_id,
                    "name": stage.executor_subject_name,
                },
                "owner_user_id": stage.owner_user_id,
                "completion_gate": json.loads(stage.completion_gate_json),
            }
            for stage in sorted(stages, key=lambda item: item.stage_order)
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


MAX_DIMA_IMPORT_BATCH_SIZE = 20


class TodoDimaImportStatus(StrEnum):
    """One independently committed result for a Dima batch item."""

    IMPORTED = "imported"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass(frozen=True)
class TodoDimaImportItem:
    """Local product facts for one imported Dima work item."""

    title: str
    description: str
    source: TodoSourceInput


@dataclass(frozen=True)
class TodoDimaImportResult:
    """One stable per-item result; batch success never hides item failure."""

    source_work_item_id: str
    status: TodoDimaImportStatus
    todo_id: str | None
    product_runtime_status: TodoProductColletStatus
    error_code: str | None = None
    retryable: bool = False
