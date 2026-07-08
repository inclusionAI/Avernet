"""FileRepositoryProtocol — persistence for teclaw workspace-file metadata.

A single ORM-backed implementation lives at ``plugins/file_repository.py``; the
per-environment difference is only the injected ``DatabasePlugin`` (SQLite local,
OceanBase prod), so there is no local/prod split (mirrors ``ResourceRepository``).
"""
from __future__ import annotations

from typing import List, Optional, Protocol

from agentclaw.community.core.files.models import FileRecord


class FileRepositoryProtocol(Protocol):
    """Persistent storage for ``ac_file`` rows (teclaw workspace files)."""

    def create(self, data: dict) -> FileRecord:
        """Insert one row from a column dict; return the stored record."""
        ...

    def get_by_path(self, *, bot_id: str, env: str, path: str) -> Optional[FileRecord]:
        """Fetch the row for an exact ``path`` under ``bot_id``/``env``, or None."""
        ...

    def list_by_path_prefix(
        self, *, bot_id: str, env: str, prefix: str
    ) -> List[FileRecord]:
        """List rows whose ``path`` starts with ``prefix`` (directory subtree)."""
        ...

    def list_by_bot(
        self, *, bot_id: str, env: str, engine_type: Optional[str] = None
    ) -> List[FileRecord]:
        """List a bot's file rows in an env (compose reads this).

        When ``engine_type`` is given, restrict to rows for that engine — so a bot
        that switched engines doesn't surface stale rows from a prior engine.
        """
        ...

    def delete(self, file_id: int) -> bool:
        """Hard-delete the row by id. Returns True if a row was removed."""
        ...
