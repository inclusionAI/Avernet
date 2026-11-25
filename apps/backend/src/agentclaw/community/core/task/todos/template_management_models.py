"""Transport-neutral records for versioned Todo template management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentclaw.community.core.task.todos.template_models import (
    _DocumentedTemplateEnum,
    TodoTemplateDefinition,
)


class TodoTemplateStatus(_DocumentedTemplateEnum):
    """Lifecycle of a stable Todo template identity."""

    ACTIVE = "active"
    ARCHIVED = "archived"

    __descriptions__ = {
        "active": "The template can publish versions and create Todos.",
        "archived": "The template is read-only and can no longer create Todos.",
    }


class TodoTemplateVersionStatus(_DocumentedTemplateEnum):
    """Mutability lifecycle of one Todo template definition version."""

    DRAFT = "draft"
    PUBLISHED = "published"

    __descriptions__ = {
        "draft": "The definition is mutable and not yet usable by Todos.",
        "published": "The definition is immutable and usable by Todo creation.",
    }


@dataclass(frozen=True)
class TodoTemplateRecord:
    """Stable Todo template metadata within one Space."""

    template_id: str
    space_id: int
    name: str
    description: str
    status: TodoTemplateStatus
    published_version_id: str | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TodoTemplateVersionRecord:
    """One immutable-after-publication Todo template definition."""

    template_version_id: str
    template_id: str
    version_no: int
    status: TodoTemplateVersionStatus
    schema_version: str
    definition: TodoTemplateDefinition
    definition_hash: str
    created_by: str
    published_by: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TodoTemplateDetail:
    """Template metadata plus its ordered version history."""

    template: TodoTemplateRecord
    versions: tuple[TodoTemplateVersionRecord, ...]


@dataclass(frozen=True)
class TodoTemplatePage:
    """Database-filtered Todo template page."""

    total: int
    items: tuple[TodoTemplateRecord, ...]
