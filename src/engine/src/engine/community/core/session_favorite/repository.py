"""SQLite persistence for session favorites owned by the adapter."""
from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


def _default_database_path() -> Path:
    """Return the adapter state database path without depending on an engine."""
    state_dir = os.getenv("ENGINE_ADAPTER_STATE_DIR", "").strip()
    base_dir = Path(state_dir).expanduser() if state_dir else Path.home() / ".engine-adapter"
    return base_dir / "session_favorites.sqlite3"


class SessionFavoriteRepository:
    """Persist a user's session favorites in an adapter-local SQLite database."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path = database_path or _default_database_path()
        self._initialization_lock = threading.Lock()
        self._initialized = False

    def list_session_ids(self, user_id: str) -> list[str]:
        """Return session IDs with the most recently favorited session first."""
        self._ensure_initialized()
        with self._connect() as connection:
            # 以下为安全注释COSEC：使用参数化查询防止 SQL 注入，禁止字符串拼接
            rows = connection.execute(
                """
                SELECT session_id
                FROM session_favorites
                WHERE user_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (user_id,),
            ).fetchall()
        return [row["session_id"] for row in rows]

    def add(self, user_id: str, session_id: str) -> None:
        self._ensure_initialized()
        with self._connect() as connection:
            # 以下为安全注释COSEC：使用参数化查询防止 SQL 注入，禁止字符串拼接
            connection.execute(
                """
                INSERT INTO session_favorites (user_id, session_id)
                VALUES (?, ?)
                ON CONFLICT(user_id, session_id) DO NOTHING
                """,
                (user_id, session_id),
            )

    def remove(self, user_id: str, session_id: str) -> bool:
        self._ensure_initialized()
        with self._connect() as connection:
            # 以下为安全注释COSEC：使用参数化查询防止 SQL 注入，禁止字符串拼接
            cursor = connection.execute(
                "DELETE FROM session_favorites WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
        return cursor.rowcount > 0

    def remove_session(self, session_id: str) -> int:
        """Remove every user's favorite record after the engine deletes a session."""
        self._ensure_initialized()
        with self._connect() as connection:
            # 以下为安全注释COSEC：使用参数化查询防止 SQL 注入，禁止字符串拼接
            cursor = connection.execute(
                "DELETE FROM session_favorites WHERE session_id = ?",
                (session_id,),
            )
        return cursor.rowcount

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_favorites (
                        user_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, session_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_session_favorites_session_id
                    ON session_favorites (session_id)
                    """
                )
            self._initialized = True

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


_repository: SessionFavoriteRepository | None = None
_repository_lock = threading.Lock()


def get_session_favorite_repository() -> SessionFavoriteRepository:
    """Return the process-wide adapter metadata repository."""
    global _repository
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = SessionFavoriteRepository()
    return _repository
