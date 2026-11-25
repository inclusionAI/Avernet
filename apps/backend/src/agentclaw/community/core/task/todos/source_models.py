"""Normalized external-source models owned by the Todo product domain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TodoSourceWorkItemKind = Literal["req", "task", "bug"]

MAX_TODO_SOURCE_TYPE_LENGTH = 32
MAX_TODO_SOURCE_CONTAINER_ID_LENGTH = 128
MAX_TODO_SOURCE_ITEM_ID_LENGTH = 128
MAX_TODO_SOURCE_URL_LENGTH = 2_048
MAX_TODO_SOURCE_UPDATED_AT_LENGTH = 128


@dataclass(frozen=True)
class TodoSourceInput:
    """Stable source identity attached atomically when a Todo is imported."""

    source_type: Literal["dima"]
    container_id: str
    item_id: str
    url: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class TodoSourceProject:
    id: str
    name: str


@dataclass(frozen=True)
class TodoSourceWorkItem:
    id: str
    project_id: str
    category: TodoSourceWorkItemKind
    title: str
    status: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class TodoSourceDraft:
    source_type: Literal["dima"]
    project_id: str
    work_item_id: str
    category: TodoSourceWorkItemKind
    title: str
    description: str
    source_url: str | None = None
    source_updated_at: str | None = None
