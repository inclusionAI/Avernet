"""Normalized external-source models owned by the Todo product domain."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

TodoSourceWorkItemKind = Literal["req", "task", "bug"]
TodoSourceContainerKind = Literal["workspace", "project"]

MAX_TODO_SOURCE_TYPE_LENGTH = 32
MAX_TODO_SOURCE_CONTAINER_ID_LENGTH = 128
MAX_TODO_SOURCE_ITEM_ID_LENGTH = 128
MAX_TODO_SOURCE_URL_LENGTH = 2_048
MAX_TODO_SOURCE_UPDATED_AT_LENGTH = 128
MAX_TODO_SOURCE_METADATA_ENTRIES = 20
MAX_TODO_SOURCE_METADATA_KEY_LENGTH = 64
MAX_TODO_SOURCE_METADATA_VALUE_LENGTH = 512
MAX_TODO_SOURCE_METADATA_JSON_BYTES = 4_096


def normalize_todo_source_metadata(value: object) -> dict[str, str]:
    """Normalize the bounded flat JSON snapshot stored with an imported source."""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("source metadata must be an object")
    if len(value) > MAX_TODO_SOURCE_METADATA_ENTRIES:
        raise ValueError("source metadata has too many entries")

    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise ValueError("source metadata keys and values must be strings")
        key = raw_key.strip()
        metadata_value = raw_value.strip()
        if not key:
            raise ValueError("source metadata key must not be empty")
        if len(key) > MAX_TODO_SOURCE_METADATA_KEY_LENGTH:
            raise ValueError("source metadata key exceeds the maximum length")
        if len(metadata_value) > MAX_TODO_SOURCE_METADATA_VALUE_LENGTH:
            raise ValueError("source metadata value exceeds the maximum length")
        if key in normalized:
            raise ValueError("source metadata contains duplicate normalized keys")
        if metadata_value:
            normalized[key] = metadata_value

    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_TODO_SOURCE_METADATA_JSON_BYTES:
        raise ValueError("source metadata exceeds the maximum JSON size")
    return normalized


@dataclass(frozen=True)
class TodoSourceInput:
    """Stable source identity attached atomically when a Todo is imported."""

    source_type: Literal["dima"]
    container_id: str
    item_id: str
    url: str | None = None
    updated_at: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TodoSourceProject:
    """A Dima workspace or project visible to the authenticated Human."""

    id: str
    name: str
    kind: TodoSourceContainerKind = "project"


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
    metadata: dict[str, str] = field(default_factory=dict)
