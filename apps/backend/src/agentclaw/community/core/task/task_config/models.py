"""Domain record for ``task_config`` (spec 2026-11-02)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class TaskConfigRecord:
    """One typed config row keyed by (config_name, env).

    ``config_value`` is the raw stored JSON text — the repository does not
    interpret it; the service encodes/validates per ``config_type``.
    """

    id: int
    config_name: str
    config_type: str
    config_value: str
    env: str
    creator: str | None
    modifier: str | None
    gmt_create: datetime | None
    gmt_modify: datetime | None
