"""Pydantic model for an ``ac_file`` row — teclaw workspace-file metadata."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FileRecord(BaseModel):
    """One teclaw bot workspace file's metadata.

    ``path`` is relative to the bot's data dir (e.g. ``docs/a.md``) — the same
    relative path compose joins with the data dir to emit the artifact ref.
    """

    id: Optional[int] = None
    bot_id: str
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    engine_type: Optional[str] = None
    env: str = "dev"
    path: str
    name: str
    parent_path: Optional[str] = None
    size: int = 0
    mime_type: Optional[str] = None
    source: Optional[str] = None
    created_by: Optional[str] = None
    user_id: Optional[str] = None
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None
