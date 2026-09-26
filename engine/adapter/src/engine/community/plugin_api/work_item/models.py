"""Neutral work-item DTOs shared across the API and engine layers.

These types carry no vendor semantics. ``space_ref`` and ``WorkItemRef.url`` are
opaque strings whose meaning is interpreted only inside a ``WorkItemService``
implementation; callers never see vendor identifiers such as a workspace id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkItemRef:
    """Neutral identifier for a single work item — an opaque URL string."""

    url: str


@dataclass(frozen=True)
class WorkItemCreate:
    """Neutral request body for creating a work item."""

    staff_id: str
    space_ref: str
    subject: str
    content: str = ""
    item_type: str = "task"
    priority: str = "P2"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkItem:
    """Neutral work-item view. Vendor payload passes through unmodified in ``raw``."""

    id: str
    url: str
    subject: str
    content: str
    raw: dict[str, Any]
